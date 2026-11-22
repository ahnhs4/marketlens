"""Authentication, sessions, rate limiting and input validation helpers.

* Passwords: scrypt (stdlib) with per-user salt; constant-time comparison.
* Sessions: signed, time-limited token in an HttpOnly, SameSite=Lax cookie (Secure in production).
  Logging out everywhere bumps `session_version`, invalidating outstanding tokens.
* AUTH_REQUIRED=false (default for local use) maps anonymous requests to a single local user.
* Rate limiting: per-client token bucket (in-process; put a gateway limiter in front in production).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import threading
import time

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, UserSettings

COOKIE = "ml_session"
LOCAL_EMAIL = "local@marketlens.local"
TICKER_RE = re.compile(r"^[A-Za-z0-9\^\.\-=]{1,20}$")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, dk_b64 = stored.split("$")
    dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="ml-session")


def issue_session(resp: Response, user: User) -> None:
    s = get_settings()
    token = _serializer().dumps({"uid": user.id, "v": user.session_version})
    resp.set_cookie(COOKIE, token, max_age=s.session_ttl_hours * 3600, httponly=True, samesite="lax",
                    secure=s.is_production, path="/")


def clear_session(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/")


def _user_from_cookie(request: Request, db: Session) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=get_settings().session_ttl_hours * 3600)
    except (BadSignature, SignatureExpired):
        return None
    u = db.get(User, data.get("uid"))
    if not u or not u.is_active or u.session_version != data.get("v"):
        return None
    return u


def local_user(db: Session) -> User:
    u = db.execute(select(User).where(User.email == LOCAL_EMAIL)).scalar_one_or_none()
    if u is None:
        u = User(email=LOCAL_EMAIL, display_name="Local user")
        db.add(u)
        db.flush()
        db.add(UserSettings(user_id=u.id))
        db.commit()
    return u


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    u = _user_from_cookie(request, db)
    if u:
        return u
    if not get_settings().auth_required:
        return local_user(db)
    raise HTTPException(status_code=401, detail="Authentication required")


def valid_ticker(t: str) -> str:
    t = (t or "").strip()
    if not t or len(t) > 40:
        raise HTTPException(status_code=400, detail="Invalid ticker")
    return t


class RateLimiter:
    """Token bucket per (client, bucket). Cheap enough for an in-process guard."""

    def __init__(self) -> None:
        self._b: dict[tuple[str, str], tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, bucket: str, per_minute: int) -> bool:
        now = time.monotonic()
        rate = per_minute / 60.0
        with self._lock:
            tokens, last = self._b.get((client, bucket), (float(per_minute), now))
            tokens = min(float(per_minute), tokens + (now - last) * rate)
            if tokens < 1:
                self._b[(client, bucket)] = (tokens, now)
                return False
            self._b[(client, bucket)] = (tokens - 1, now)
            if len(self._b) > 50_000:
                self._b.clear()
            return True


limiter = RateLimiter()
EXPENSIVE = ("/backtest", "/walk-forward", "/screener", "/portfolio/stress", "/admin/", "/builder/run", "/rotation/prepare")
