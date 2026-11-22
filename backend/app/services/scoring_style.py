"""Which scoring style is active (설정 > 채점 방식): 'user' (내 규칙, default) or 'evidence' (데이터 검증).

Read from the local user's preferences and cached briefly; changing it clears the cached technical results
and rescans the recommendation universe so every page switches together.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from ..analysis import scoring
from ..cache import cache, cached
from ..db import SessionLocal
from ..models import User
from ..security import LOCAL_EMAIL

log = logging.getLogger(__name__)


def _load() -> str:
    from . import settings_store
    with SessionLocal() as db:
        u = db.execute(select(User).where(User.email == LOCAL_EMAIL)).scalar_one_or_none() or \
            db.execute(select(User).order_by(User.id)).scalars().first()
        if u is None:
            return "user"
        return settings_store.get(db, u)["preferences"].get("timing_style") or "user"


def current() -> str:
    return cached("scoring-style", 15, _load)


def install() -> None:
    scoring.STYLE_PROVIDER["get"] = current


def changed() -> None:
    """Drop everything scored under the old style and rescan in the background."""
    for prefix in ("scoring-style", "tech:", "scores:", "outlook:"):
        cache.delete_prefix(prefix)
    from . import universe
    threading.Thread(target=lambda: universe.scan("ALL"), name="rescan-style", daemon=True).start()
