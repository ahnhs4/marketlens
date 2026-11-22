"""Small persistence helpers (dialect-aware upsert, JSON-safe conversion)."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session


def upsert(db: Session, model, rows: list[dict], keys: list[str], chunk: int = 500) -> int:
    """INSERT ... ON CONFLICT (keys) DO UPDATE for SQLite and PostgreSQL."""
    if not rows:
        return 0
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    cols = [c for c in rows[0].keys() if c not in keys]
    n = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        stmt = insert(model).values(part)
        if cols:
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_={c: getattr(stmt.excluded, c) for c in cols})
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=keys)
        db.execute(stmt)
        n += len(part)
    return n


def clean(obj: Any) -> Any:
    """Recursively convert numpy/pandas values to JSON-safe Python (NaN/inf -> None)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, pd.Series):
        return [clean(v) for v in obj.tolist()]
    return obj


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime | None) -> str | None:
    return ts.astimezone(dt.timezone.utc).isoformat() if ts else None
