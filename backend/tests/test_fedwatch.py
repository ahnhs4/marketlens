import datetime as dt

import pytest

from app.cache import cache
from app.services import fedwatch as fw

D = dt.date
MEETS = [D(2026, 10, 28), D(2026, 12, 9), D(2027, 1, 27)]
EFFR, LOWER = 3.88, 3.75


def px(rates: dict[str, float]) -> dict[str, float]:
    """Futures prices from month-average rates (a contract settles at 100 - the average rate)."""
    return {k: 100 - v for k, v in rates.items()}


def avg(before: float, after: float, day: int, n: int) -> float:
    """Month-average rate when the rate changes after the meeting on `day` of an `n`-day month."""
    return (before * day + after * (n - day)) / n


def test_nothing_priced_is_hold():
    flat = px({"2026-10": EFFR, "2026-11": EFFR, "2026-12": EFFR, "2027-01": EFFR, "2027-02": EFFR})
    rows = fw.compute(flat, MEETS, EFFR, LOWER, D(2026, 10, 15))
    assert [r["date"] for r in rows] == ["2026-10-28", "2026-12-09", "2027-01-27"]
    for r in rows:
        assert r["meeting"]["hold"] == pytest.approx(100)
        assert r["most_likely"] == {"lower": 3.75, "upper": 4.0, "steps": 0, "prob": 100.0}


def test_full_hike_then_hold():
    hi = EFFR + 0.25
    prices = px({"2026-10": avg(EFFR, hi, 28, 31), "2026-11": hi, "2026-12": hi, "2027-01": hi, "2027-02": hi})
    oct_, dec, _ = fw.compute(prices, MEETS, EFFR, LOWER, D(2026, 10, 15))
    assert oct_["meeting"]["hike"] == pytest.approx(100)
    assert oct_["change_bp"] == pytest.approx(25)
    assert dec["meeting"]["hold"] == pytest.approx(100)          # no further move priced
    assert dec["vs_today"]["hike"] == pytest.approx(100)         # but still a hike versus today
    assert dec["most_likely"]["lower"] == 4.0


def test_half_priced_hike_splits_the_odds():
    mid = EFFR + 0.125
    prices = px({"2026-10": avg(EFFR, mid, 28, 31), "2026-11": mid, "2026-12": mid, "2027-01": mid, "2027-02": mid})
    oct_ = fw.compute(prices, MEETS, EFFR, LOWER, D(2026, 10, 15))[0]
    assert oct_["meeting"]["hike"] == pytest.approx(50, abs=0.1)
    assert oct_["meeting"]["hold"] == pytest.approx(50, abs=0.1)
    assert {(r["lower"], r["prob"]) for r in oct_["ranges"]} == {(3.75, 50.0), (4.0, 50.0)}


def test_meeting_month_followed_by_meeting_month_uses_own_contract():
    # Dec and Jan both have meetings, so Dec's rate-after is backed out of the Dec contract itself.
    hi = EFFR + 0.25
    prices = px({"2026-10": EFFR, "2026-11": EFFR, "2026-12": avg(EFFR, hi, 9, 31), "2027-01": hi, "2027-02": hi})
    _, dec, jan = fw.compute(prices, MEETS, EFFR, LOWER, D(2026, 10, 15))
    assert dec["rate_after"] == pytest.approx(hi)
    assert dec["meeting"]["hike"] == pytest.approx(100)
    assert jan["meeting"]["hold"] == pytest.approx(100)


def test_expired_meeting_contract_is_folded_into_the_next_meeting():
    # A month ago the September contract had expired; the Sep 16 hike is recovered from Oct + Nov.
    hi = EFFR + 0.25
    meets = [D(2026, 9, 16), *MEETS]
    prices = px({"2026-10": hi, "2026-11": hi, "2026-12": hi, "2027-01": hi, "2027-02": hi})
    rows = fw.compute(prices, meets, EFFR, LOWER, D(2026, 9, 15))
    assert rows[0]["date"] == "2026-10-28"
    assert rows[0]["rate_before"] == pytest.approx(hi)
    assert rows[0]["meeting"]["hold"] == pytest.approx(100)
    assert rows[0]["vs_today"]["hike"] == pytest.approx(100, abs=0.1)


def test_missing_contract_stops_instead_of_guessing():
    rows = fw.compute(px({"2026-10": EFFR, "2026-11": EFFR}), MEETS, EFFR, LOWER, D(2026, 10, 15))
    assert [r["date"] for r in rows] == ["2026-10-28"]


def test_meeting_calendar_parsing(monkeypatch):
    html = """<h4>2026 FOMC Meetings</h4>
      <div class="fomc-meeting__month col-xs-5"> <strong>September</strong></div>
      <div class="fomc-meeting__date col-xs-4">15-16</div>
      <div class="fomc-meeting__month col-xs-5"> <strong>Oct/Nov</strong></div>
      <div class="fomc-meeting__date col-xs-4">31-1*</div>
      <div class="fomc-meeting__month col-xs-5"> <strong>December</strong></div>
      <div class="fomc-meeting__date col-xs-4">8-9*</div>
      <h4>2027 FOMC Meetings</h4>
      <div class="fomc-meeting__month col-xs-5"> <strong>January</strong></div>
      <div class="fomc-meeting__date col-xs-4">26-27</div>"""

    class R:
        text = html
        def raise_for_status(self):
            pass
    monkeypatch.setattr(fw.httpx, "get", lambda *a, **k: R())
    cache.delete_prefix("fedwatch:meetings")
    try:
        assert fw.meetings() == [D(2026, 9, 16), D(2026, 11, 1), D(2026, 12, 9), D(2027, 1, 27)]
    finally:
        cache.delete_prefix("fedwatch:meetings")
