"""Alert conditions, evaluation job and notification channels.

Channels: web (stored event, polled by the UI which raises a browser Notification),
email (SMTP), Discord webhook, Telegram bot. Channels without configuration are
skipped and the event records why.
"""
from __future__ import annotations

import datetime as dt
import logging
import smtplib
from email.message import EmailMessage

import httpx
import pandas as pd
from sqlalchemy import select

from ..analysis import indicators, levels
from ..config import get_settings
from ..db import SessionLocal
from ..models import Alert, AlertEvent, News, NewsAnalysis, User
from . import prices
from .store import clean, utcnow

log = logging.getLogger(__name__)

CONDITIONS = {
    "rsi_above": {"label": "RSI 이상", "params": {"value": 70}},
    "rsi_below": {"label": "RSI 이하", "params": {"value": 30}},
    "price_above": {"label": "가격 이상", "params": {"value": None}},
    "price_below": {"label": "가격 이하", "params": {"value": None}},
    "price_cross_sma": {"label": "이동평균선 돌파/이탈", "params": {"period": 200, "direction": "any"}},
    "golden_cross": {"label": "골든크로스 (SMA50/200)", "params": {}},
    "death_cross": {"label": "데드크로스 (SMA50/200)", "params": {}},
    "breaks_resistance": {"label": "저항선(R1) 돌파", "params": {}},
    "breaks_support": {"label": "지지선(S1) 이탈", "params": {}},
    "volume_spike": {"label": "거래량 20일 평균의 N배 이상", "params": {"multiple": 2.0}},
    "technical_score_above": {"label": "기술 점수 이상", "params": {"value": 85}},
    "technical_score_below": {"label": "기술 점수 이하", "params": {"value": 30}},
    "major_news": {"label": "주요 뉴스 (영향도 높음 이상)", "params": {"hours": 24}},
}


def _ind(symbol: str) -> pd.DataFrame:
    return indicators.compute_all(prices.get_daily(symbol, years=2).df)


def evaluate(alert: Alert) -> tuple[bool, str, dict]:
    s, ct, p = alert.symbol, alert.condition_type, alert.params or {}
    if ct == "major_news":
        since = utcnow() - dt.timedelta(hours=float(p.get("hours", 24)))
        last = alert.last_triggered_at or since
        with SessionLocal() as db:
            rows = db.execute(select(News, NewsAnalysis).join(NewsAnalysis, NewsAnalysis.news_id == News.id)
                              .where(News.published_at >= max(since, last), NewsAnalysis.impact.in_(["High", "Critical"]))).all()
        hits = [(n, a) for n, a in rows if s in (n.tickers or []) or s in (a.tickers or [])]
        if hits:
            n, a = hits[0]
            return True, f"{s}: 영향도 {'매우 높음' if a.impact == 'Critical' else '높음'} 뉴스 - {n.title[:140]}", {"news_id": n.id, "url": n.url}
        return False, "", {}
    ind = _ind(s)
    last, prev = ind.iloc[-1], ind.iloc[-2]
    price = float(last["close"])
    if ct in ("rsi_above", "rsi_below"):
        v = float(p.get("value", 70 if ct == "rsi_above" else 30))
        hit = last["rsi"] > v if ct == "rsi_above" else last["rsi"] < v
        return bool(hit), f"{s}: RSI {last['rsi']:.1f} ({v:g} {'초과' if ct == 'rsi_above' else '미만'})", {"rsi": float(last["rsi"])}
    if ct in ("price_above", "price_below"):
        v = float(p["value"])
        hit = price > v if ct == "price_above" else price < v
        return hit, f"{s}: 가격 {price:,.2f} ({v:g} {'초과' if ct == 'price_above' else '미만'})", {"price": price}
    if ct == "price_cross_sma":
        n = int(p.get("period", 200))
        col = indicators.sma(ind["close"], n)
        a, b = prev["close"] - col.iloc[-2], last["close"] - col.iloc[-1]
        d = p.get("direction", "any")
        up, dn = a <= 0 < b, a >= 0 > b
        hit = (up and d in ("any", "up")) or (dn and d in ("any", "down"))
        return bool(hit), f"{s}: 주가가 SMA{n}({col.iloc[-1]:,.2f}) {'상향 돌파' if up else '하향 이탈'}", {"price": price}
    if ct in ("golden_cross", "death_cross"):
        a, b = prev["sma50"] - prev["sma200"], last["sma50"] - last["sma200"]
        hit = (a <= 0 < b) if ct == "golden_cross" else (a >= 0 > b)
        return bool(hit), f"{s}: {'골든크로스' if ct == 'golden_cross' else '데드크로스'} 발생 (SMA50/SMA200)", {}
    if ct in ("breaks_resistance", "breaks_support"):
        sr = levels.support_resistance(ind.iloc[:-1])  # levels known before today's bar
        if ct == "breaks_resistance":
            lv = sr["resistances"][0]
            hit = not lv.get("projected") and price > lv["price"] >= prev["close"]
        else:
            lv = sr["supports"][0]
            hit = not lv.get("projected") and price < lv["price"] <= prev["close"]
        return bool(hit), f"{s}: {'저항선' if ct == 'breaks_resistance' else '지지선'} {lv['price']:,.2f} {'돌파' if ct == 'breaks_resistance' else '이탈'}", {"level": lv["price"]}
    if ct == "volume_spike":
        m = float(p.get("multiple", 2.0))
        hit = last["vol_ratio"] >= m
        return bool(hit), f"{s}: 거래량 20일 평균의 {last['vol_ratio']:.1f}배", {"vol_ratio": float(last["vol_ratio"])}
    if ct in ("technical_score_above", "technical_score_below"):
        from .stock import technicals
        sc = technicals(s)["signal"]["score"]
        v = float(p.get("value", 85 if ct.endswith("above") else 30))
        hit = sc > v if ct.endswith("above") else sc < v
        return hit, f"{s}: 기술 점수 {sc:.0f} ({v:g} {'초과' if ct.endswith('above') else '미만'})", {"score": sc}
    return False, "", {}


# ------------------------------------------------------------- notifiers
def _send_email(to: str, subject: str, body: str) -> str:
    s = get_settings()
    if not (s.smtp_host and s.smtp_from):
        return "건너뜀: SMTP 미설정"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user and s.smtp_password:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return "전송됨"
    except Exception as e:
        return f"실패: {e}"


def _send_discord(text: str) -> str:
    url = get_settings().discord_webhook_url
    if not url:
        return "건너뜀: DISCORD_WEBHOOK_URL 미설정"
    try:
        httpx.post(url, json={"content": text[:1900]}, timeout=10).raise_for_status()
        return "전송됨"
    except Exception as e:
        return f"실패: {e}"


def _send_telegram(text: str) -> str:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return "건너뜀: 텔레그램 미설정"
    try:
        httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                   json={"chat_id": s.telegram_chat_id, "text": text[:4000]}, timeout=10).raise_for_status()
        return "전송됨"
    except Exception as e:
        return f"실패: {e}"


def dispatch(alert: Alert, user_email: str | None, message: str) -> dict:
    out = {}
    for ch in alert.channels or ["web"]:
        if ch == "web":
            out["web"] = "저장됨"
        elif ch == "email":
            out["email"] = _send_email(user_email, f"[MarketLens] {alert.symbol} 알림", message) if user_email else "건너뜀: 이메일 없음"
        elif ch == "discord":
            out["discord"] = _send_discord(message)
        elif ch == "telegram":
            out["telegram"] = _send_telegram(message)
    return out


def run_checks() -> dict:
    now = utcnow()
    fired, checked, errors = 0, 0, 0
    with SessionLocal() as db:
        alerts = db.execute(select(Alert).where(Alert.is_active.is_(True))).scalars().all()
        for a in alerts:
            if a.last_triggered_at and now - a.last_triggered_at < dt.timedelta(minutes=a.cooldown_minutes):
                continue
            checked += 1
            try:
                hit, msg, data = evaluate(a)
            except Exception as e:
                errors += 1
                log.info("alert %s evaluation failed: %s", a.id, e)
                continue
            a.last_checked_at = now
            if hit:
                user = db.get(User, a.user_id)
                delivered = dispatch(a, user.email if user and "@" in (user.email or "") and not user.email.endswith("@marketlens.local") else None, msg)
                db.add(AlertEvent(alert_id=a.id, message=msg, data=clean(data), delivered=delivered))
                a.last_triggered_at = now
                fired += 1
        db.commit()
    return {"checked": checked, "fired": fired, "errors": errors, "at": now.isoformat()}
