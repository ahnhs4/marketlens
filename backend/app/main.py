"""FastAPI application entry point."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from . import jobs
from .api import lab, market, stock, user
from .api import paper as paper_api
from .config import BACKEND_DIR, get_settings
from .security import EXPENSIVE, limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("yfinance", "httpx", "apscheduler.executors.default", "peewee"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("marketlens")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False  # keep the app's logging configuration
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.is_production and s.secret_key == "dev-only-change-me":
        raise RuntimeError("SECRET_KEY must be set in production")
    run_migrations()
    from .services import scoring_style
    scoring_style.install()
    jobs.start()
    yield
    jobs.stop()


app = FastAPI(title="MarketLens API", version="0.1.0", lifespan=lifespan,
              description="Market, macro, news and technical analysis for US and Korean equities.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])
app.add_middleware(GZipMiddleware, minimum_size=2048)  # chart payloads carry years of bars


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    client = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    path = request.url.path
    s = get_settings()
    if path.startswith("/api/"):
        expensive = any(x in path for x in EXPENSIVE) and request.method == "POST"
        bucket, per_min = ("expensive", 20) if expensive else ("api", s.rate_limit_per_minute)
        if not limiter.allow(client, bucket, per_min):
            return JSONResponse({"detail": {"code": "rate_limited", "message": "요청이 너무 많습니다 - 잠시 후 다시 시도하세요"}},
                                status_code=429, headers={"Retry-After": "10"})
    t0 = time.perf_counter()
    resp = await call_next(request)
    dur = (time.perf_counter() - t0) * 1000
    resp.headers["X-Response-Time-ms"] = f"{dur:.0f}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    if dur > 3000:
        log.info("slow request %s %s %.0fms", request.method, path, dur)
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    errs = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse({"detail": {"code": "invalid_input", "message": "잘못된 요청입니다", "errors": errs}}, status_code=422)


app.include_router(market.router)
app.include_router(stock.router)
app.include_router(user.router)
app.include_router(paper_api.router)
app.include_router(lab.router)
