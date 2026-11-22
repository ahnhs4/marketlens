"""The user's own entry-timing rules (채점 방식 '내 규칙', the default since 2026-10-11).

The user's view: buying an overbought stock at its high is already late. Turnarounds from oversold and
golden crosses are early. These rules are scored large on purpose and apply to both markets. They are the
user's rules, not measured edges; the app's US/KR measurements (timing_evidence.py) still exist as the
'데이터 검증' mode in 설정.

Points (summed, the timing score is 50 + Σ, clamped to 0~100):
  RSI, each of the three horizons (단기 / 중기 / 장기 periods from 설정):
      ≥ 80  −9 / −11 / −13      70~80  −6 / −8 / −10
      ≤ 20  +9 / +11 / +13      20~30  +6 / +8 / +10
  two horizons overbought −6, all three −12 (oversold: +6 / +12)
  losing strength: an overbought horizon whose RSI fell over the last 3 bars −5 (힘 빠짐)
  turning up: an oversold horizon whose RSI rose over the last 3 bars +6 (반등 시작)
  52-week high: within 2% −15, 2~5% −10, 5~10% −5; at the all-time high of the loaded history −5 more
  real resistance (not a projected level) within 3% −8
  golden cross 5/20 within 5 bars +6, 20/60 within 10 bars +10, 50/200 within 20 bars +14
  (dead crosses the same, negative); a 20/60 or 50/200 golden cross about to happen (gap < 1% and
  closing) +6
  slow stochastic: medium AND long %K both ≥ 80 −10 (and such stocks are left out of every ranking);
  both ≤ 20 +10
Rankings also leave out (2026-11-05, the user's rule) a stock whose medium (14) or long (28) RSI is oversold (≤ 30), or
just above it (30~35) and rising (higher than 3 bars ago). The timing points above are unchanged; only the ranking
tables drop them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STOCH_OB, STOCH_OS = 80.0, 20.0
RSI_OS, RSI_NEAR_OS = 30.0, 35.0
RSI_ML = (("rsi", "중기 RSI(14)"), ("rsi_long", "장기 RSI(28)"))


def stoch_ml(row) -> tuple[float | None, float | None]:
    """(medium %K, long %K) of a bar - `row` is an indicator row or a dict with those keys."""
    def get(k):
        v = row.get(k) if hasattr(row, "get") else None
        return float(v) if v is not None and pd.notna(v) else None
    return get("stoch_medium_k"), get("stoch_long_k")


def stoch_ml_overbought(row) -> bool:
    """Medium and long slow stochastics both overbought - the user wants these out of the rankings."""
    m, lg = stoch_ml(row)
    return m is not None and lg is not None and m >= STOCH_OB and lg >= STOCH_OB


def rsi_ml_oversold(ind: pd.DataFrame) -> list[str]:
    """Why a stock is out of the rankings under the RSI rule: medium or long RSI oversold, or near it and rising.
    Empty list = not excluded."""
    out = []
    for col, name in RSI_ML:
        if col not in ind or len(ind) < 4:
            continue
        v, back = ind[col].iloc[-1], ind[col].iloc[-4]
        if pd.isna(v):
            continue
        if v <= RSI_OS:
            out.append(f"{name} {v:.1f} 과매도")
        elif v <= RSI_NEAR_OS and pd.notna(back) and v > back:
            out.append(f"{name} {v:.1f} 과매도 직전에서 상승 중 (3일 전 {back:.1f})")
    return out


RSI_PTS = {"rsi_short": (6, 9), "rsi": (8, 11), "rsi_long": (10, 13)}   # (70~80 / 20~30, ≥80 / ≤20)
HZ_KO = {"rsi_short": "단기", "rsi": "중기", "rsi_long": "장기"}
CROSS = [(5, 20, 5, 6), (20, 60, 10, 10), (50, 200, 20, 14)]          # fast, slow, within bars, points


def _last_cross(fast: pd.Series, slow: pd.Series, within: int) -> tuple[str, int] | None:
    d = (fast - slow).dropna()
    if len(d) < within + 2:
        return None
    s = np.sign(d.to_numpy())
    for back in range(0, within):
        i = len(s) - 1 - back
        if s[i] > 0 >= s[i - 1]:
            return "golden", back
        if s[i] < 0 <= s[i - 1]:
            return "dead", back
    return None


def signals(ind: pd.DataFrame, sr: dict | None, periods: dict | None = None) -> list[tuple[float, str]]:
    """Signed points with Korean reasons for the latest bar."""
    r = ind.iloc[-1]
    c = float(r["close"])
    periods = periods or {}
    out: list[tuple[float, str]] = []
    over, under = [], []
    for col, (p1, p2) in RSI_PTS.items():
        if col not in ind:
            continue
        s = ind[col].dropna()
        if len(s) < 4:
            continue
        v, v3 = float(s.iloc[-1]), float(s.iloc[-4])
        name = f"RSI {HZ_KO[col]}({periods.get(col, '')})".replace("()", "")
        if v >= 70:
            pts = -(p2 if v >= 80 else p1)
            over.append(col)
            out.append((pts, f"{name} {v:.0f} 과매수"))
            if v < v3:
                out.append((-5, f"{name} 과매수에서 꺾임 ({v3:.0f}→{v:.0f}, 힘 빠짐)"))
        elif v <= 30:
            pts = p2 if v <= 20 else p1
            under.append(col)
            out.append((pts, f"{name} {v:.0f} 과매도"))
            if v > v3:
                out.append((6, f"{name} 과매도에서 반등 시작 ({v3:.0f}→{v:.0f})"))
    if len(over) >= 2:
        out.append((-12 if len(over) == 3 else -6, f"단기·중기·장기 중 {len(over)}개 기간 동시 과매수"))
    if len(under) >= 2:
        out.append((12 if len(under) == 3 else 6, f"단기·중기·장기 중 {len(under)}개 기간 동시 과매도"))

    sm, sl = stoch_ml(r)
    if sm is not None and sl is not None:
        if sm >= STOCH_OB and sl >= STOCH_OB:
            out.append((-10, f"중기·장기 스토캐스틱 동시 과매수 ({sm:.0f} · {sl:.0f}) - 순위 제외"))
        elif sm <= STOCH_OS and sl <= STOCH_OS:
            out.append((10, f"중기·장기 스토캐스틱 동시 과매도 ({sm:.0f} · {sl:.0f})"))

    hi52 = r.get("hi52")
    if pd.notna(hi52) and hi52:
        gap = (c / float(hi52) - 1) * 100
        if gap >= -2:
            out.append((-15, f"52주 고점 {abs(gap):.1f}% 이내 - 고점권"))
        elif gap >= -5:
            out.append((-10, f"52주 고점 {abs(gap):.1f}% 아래 - 고점 근처"))
        elif gap >= -10:
            out.append((-5, f"52주 고점 {abs(gap):.1f}% 아래"))
    ath = float(ind["high"].max()) if "high" in ind else None
    if ath and c >= ath * 0.98:
        out.append((-5, f"불러온 기간 역대 고점 {ath:,.2f} 부근"))
    if sr:
        nr = (sr.get("resistances") or [None])[0]
        if nr and not nr.get("projected") and nr.get("distance_pct") is not None and nr["distance_pct"] <= 3:
            out.append((-8, f"저항선 {nr['price']:,.2f}까지 {nr['distance_pct']:.1f}%"))

    for f, s_, within, pts in CROSS:
        fc, sc = f"sma{f}", f"sma{s_}"
        if fc not in ind or sc not in ind:
            continue
        hit = _last_cross(ind[fc], ind[sc], within)
        if hit:
            kind, ago = hit
            when = "오늘" if ago == 0 else f"{ago}일 전"
            out.append((pts if kind == "golden" else -pts,
                        f"{'골든크로스' if kind == 'golden' else '데드크로스'} {f}/{s_}일선 ({when})"))
        elif (f, s_) in ((20, 60), (50, 200)):
            fv, sv = ind[fc].dropna(), ind[sc].dropna()
            if len(fv) > 4 and len(sv) > 4 and fv.iloc[-1] < sv.iloc[-1]:
                gap_now = sv.iloc[-1] / fv.iloc[-1] - 1
                gap_before = sv.iloc[-4] / fv.iloc[-4] - 1
                if gap_now < 0.01 and gap_now < gap_before:
                    out.append((6, f"{f}/{s_}일선 골든크로스 임박 (간격 {gap_now * 100:.1f}%, 좁혀지는 중)"))
    return out
