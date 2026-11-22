"""How a strategy did in each market regime: split the equity curve's days by a regime label.

Each dimension is a daily label series (rates up/flat/down, fear/greed band, risk-on/off). For every
label we report the strategy's return over just those days, buy-and-hold over the same days, and the
trades that were ENTERED while the label held. Small samples are flagged rather than interpreted.
"""
from __future__ import annotations

import pandas as pd

MIN_TRADES = 5      # fewer entries than this in a bucket -> "표본 부족"
MIN_DAYS = 60


def _ann(r: pd.Series) -> tuple[float | None, float | None]:
    """(compounded return over the days, annualised) of a daily-return slice."""
    if not len(r):
        return None, None
    tot = float((1 + r).prod() - 1)
    ann = (1 + tot) ** (252 / len(r)) - 1 if tot > -1 else -1.0
    return tot, float(ann)


def _verdict(b: dict) -> tuple[str, str]:
    """(tone, sentence) for one bucket."""
    if b["days"] < MIN_DAYS or b["trades"] < MIN_TRADES:
        return "thin", f"표본 부족 (해당 국면 {b['days']}일 · 진입 {b['trades']}건) - 판단 보류"
    win = b["win_rate"]
    avg = b["avg_return"]
    if avg is not None and avg > 0 and win is not None and win >= 0.5:
        return "good", f"과거 이 국면에서 진입한 {b['trades']}건 중 {win * 100:.0f}%가 이익, 평균 {avg * 100:+.1f}%"
    if avg is not None and avg < 0:
        return "bad", f"과거 이 국면에서 진입한 {b['trades']}건의 평균 손익이 {avg * 100:+.1f}% (승률 {win * 100:.0f}%)"
    return "mixed", f"과거 이 국면에서 진입 {b['trades']}건 · 승률 {win * 100:.0f}% · 평균 {avg * 100:+.1f}% (뚜렷하지 않음)"


def breakdown(equity: pd.Series, hold: pd.Series | None, trades: list[dict], dims: list[dict]) -> dict:
    """dims: [{"key", "name", "labels": Series[str] by date, "order": [labels], "note": str}]."""
    r = equity.pct_change().dropna()
    hr = hold.pct_change().dropna().reindex(r.index) if hold is not None and len(hold) > 1 else None
    closed = [t for t in trades if not t.get("open")]
    out = []
    for d in dims:
        lab = d["labels"]
        if lab is None or not len(lab):
            out.append({"key": d["key"], "name": d["name"], "available": False, "note": d.get("note")})
            continue
        lab = lab[~lab.index.duplicated(keep="last")].sort_index()
        day = lab.reindex(r.index, method="ffill")
        # ffill must not reach back before the label history begins
        day[r.index < lab.index[0]] = None
        covered = day.notna()
        entry_lab = lab.reindex(pd.DatetimeIndex([pd.Timestamp(t["entry_date"]) for t in closed]), method="ffill") if closed else pd.Series(dtype=object)
        if len(entry_lab):
            entry_lab[entry_lab.index < lab.index[0]] = None
        buckets = []
        for name in d["order"]:
            m = (day == name).fillna(False).to_numpy(bool)
            tot, ann = _ann(r[m])
            htot, hann = _ann(hr[m].dropna()) if hr is not None else (None, None)
            tr = [t["return"] for t, lb in zip(closed, entry_lab.to_numpy()) if lb == name] if closed else []
            wins = [x for x in tr if x > 0]
            b = {"label": name, "days": int(m.sum()), "share": float(m.sum() / max(1, int(covered.sum()))),
                 "return": tot, "annualized": ann, "hold_return": htot, "hold_annualized": hann,
                 "trades": len(tr), "win_rate": (len(wins) / len(tr)) if tr else None,
                 "avg_return": (sum(tr) / len(tr)) if tr else None}
            b["tone"], b["verdict"] = _verdict(b)
            buckets.append(b)
        current = lab.iloc[-1] if len(lab) else None
        cur = next((b for b in buckets if b["label"] == current), None)
        out.append({"key": d["key"], "name": d["name"], "available": True, "note": d.get("note"),
                    "coverage": {"from": str(lab.index[0].date()), "to": str(lab.index[-1].date()),
                                 "share": float(covered.mean()) if len(covered) else 0.0},
                    "current": current, "current_as_of": str(lab.index[-1].date()),
                    "current_tone": cur["tone"] if cur else "thin",
                    "current_verdict": cur["verdict"] if cur else "현재 국면의 과거 기록이 없습니다",
                    "buckets": buckets})
    tones = [d["current_tone"] for d in out if d.get("available")]
    good, bad, thin = tones.count("good"), tones.count("bad"), tones.count("thin")
    if not tones or thin == len(tones):
        overall = ("thin", "지금 국면에 해당하는 과거 표본이 부족해서 이 전략을 써도 되는지 데이터로 말하기 어렵습니다.")
    elif bad and not good:
        overall = ("bad", "지금과 같은 국면에서 이 전략은 과거에 부진했습니다. 비중을 줄이거나 조건을 바꿔 다시 검증해 보세요.")
    elif good and not bad:
        overall = ("good", "지금과 같은 국면에서 이 전략은 과거에 양호했습니다. 다만 과거 성과가 앞으로를 보장하지는 않습니다.")
    else:
        overall = ("mixed", "국면 기준에 따라 결과가 엇갈립니다. 아래 표에서 부진했던 국면이 지금에 해당하는지 확인하세요.")
    return {"dimensions": out, "tone": overall[0], "verdict": overall[1],
            "method": "전략 자산곡선의 일간 수익률을 그날의 국면 라벨로 나눠 집계 · 거래는 진입일의 국면에 귀속 · "
                      f"진입 {MIN_TRADES}건 미만 또는 {MIN_DAYS}일 미만은 표본 부족으로 표시"}
