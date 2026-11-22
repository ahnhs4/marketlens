"""Shared API helpers: uniform error mapping so one failing section never breaks a page."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException

from ..providers.base import NotFoundError, ProviderError
from ..services.stock import InvalidTicker, resolve

log = logging.getLogger(__name__)
T = TypeVar("T")


def guard(fn: Callable[[], T], what: str = "data") -> T:
    try:
        return fn()
    except HTTPException:
        raise
    except InvalidTicker as e:
        raise HTTPException(404, detail={"code": "ticker_not_found", "message": str(e)})
    except NotFoundError as e:
        raise HTTPException(404, detail={"code": "not_found", "message": f"데이터를 불러올 수 없습니다 (Data unavailable): {e}"})
    except ProviderError as e:
        log.warning("provider error for %s: %s", what, e)
        raise HTTPException(503, detail={"code": "provider_unavailable", "message": "데이터를 불러올 수 없습니다 (Data unavailable) - 데이터 공급자 오류"})
    except Exception as e:  # last-resort: section-level failure, logged with stack
        log.exception("unexpected error for %s", what)
        raise HTTPException(500, detail={"code": "internal_error", "message": f"데이터를 불러올 수 없습니다 (Data unavailable, {type(e).__name__})"})


def sym(ticker: str) -> str:
    return guard(lambda: resolve(ticker), "ticker")
