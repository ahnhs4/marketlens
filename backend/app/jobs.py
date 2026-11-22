"""Background jobs (APScheduler, in-process).

For multi-instance deployments run exactly one scheduler (SCHEDULER_ENABLED=true on one
worker only) or move these jobs to Celery beat - every job is an idempotent function.
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .config import get_settings
from .services import alerts, clock, dart_fin, fear_greed, macro, news, paper, thirteenf, trend_flow, universe, valueup
from .services.symbols import index

log = logging.getLogger(__name__)
scheduler: BackgroundScheduler | None = None


def _safe(name, fn, *a):
    def run():
        try:
            fn(*a)
        except Exception:
            log.exception("job %s failed", name)
    return run


def _scan_if_due() -> None:
    open_now = clock.status("US")["state"] in ("open", "pre", "post") or clock.status("KR")["state"] == "open"
    st = universe.status()
    if st["state"] == "running":
        return
    if open_now or st["state"] in ("idle", "error"):
        universe.scan("ALL")


def _warm() -> None:
    macro.indicator_cards()
    news.market_news()
    valueup.overview()


def start() -> None:
    global scheduler
    s = get_settings()
    if not s.scheduler_enabled or scheduler is not None:
        return
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                                  "misfire_grace_time": 300})
    scheduler.add_job(_safe("symbols", index.ensure_loaded), "date", id="symbols_boot")
    scheduler.add_job(_safe("symbols_refresh", index.refresh), "interval", hours=24, id="symbols_refresh")
    scheduler.add_job(_safe("scan_boot", universe.scan, "ALL"), "date", id="scan_boot")
    scheduler.add_job(_safe("scan", _scan_if_due), "interval", minutes=s.universe_scan_minutes, id="scan")
    scheduler.add_job(_safe("warm", _warm), "interval", minutes=10, id="warm")
    scheduler.add_job(_safe("warm_boot", _warm), "date", id="warm_boot")
    scheduler.add_job(_safe("alerts", alerts.run_checks), "interval", minutes=s.alert_check_minutes, id="alerts")
    scheduler.add_job(_safe("fundamentals_boot", universe.enrich_fundamentals, 800), "date", id="fundamentals_boot",
                      run_date=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=4))
    scheduler.add_job(_safe("fundamentals", universe.enrich_fundamentals, 300), "interval", hours=6, id="fundamentals")
    # Korean market-wide daily bars for the 추세추종 flow (full fill once, then top-ups).
    scheduler.add_job(_safe("trend_history", trend_flow.ensure_kr_history), "interval", hours=6, id="trend_history",
                      next_run_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=3))
    # Deepen S&P 500 member history for the Fear & Greed breadth indicators (no-op once filled).
    scheduler.add_job(_safe("fear_greed_history", fear_greed.ensure_history), "interval", hours=24, id="fear_greed_history",
                      next_run_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2))
    # Paper trading engine (stage 1): evaluates enabled strategies on completed bars during the US session.
    scheduler.add_job(_safe("paper", paper.run_cycle), "interval", minutes=1, id="paper")
    # 13F of large hedge funds / banks: new quarters appear mid Feb / May / Aug / Nov (no-op when nothing new).
    scheduler.add_job(_safe("13f", thirteenf.ensure_filings), "interval", hours=24, id="13f",
                      next_run_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5))
    # Korean filings (OpenDART): whole-market main accounts, then full statements for watched companies. No-op without a key;
    # closed periods are skipped, open ones (filings still arriving) are re-read.
    scheduler.add_job(_safe("dart", dart_fin.daily), "interval", hours=6, id="dart",
                      next_run_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2))
    scheduler.start()
    log.info("scheduler started")


def stop() -> None:
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None
