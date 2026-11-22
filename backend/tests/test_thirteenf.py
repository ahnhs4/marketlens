import datetime as dt

from app.services import thirteenf

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>NVIDIA CORPORATION</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066G104</cusip>
    <value>1,000,000</value><shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>NVIDIA CORPORATION</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>67066g104</cusip>
    <value>500000</value><shrsOrPrnAmt><sshPrnamt>2500</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>NVIDIA CORPORATION</nameOfIssuer><titleOfClass>CALL</titleOfClass><cusip>67066G104</cusip>
    <value>900000</value><shrsOrPrnAmt><sshPrnamt>4000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Call</putCall></infoTable>
  <infoTable><nameOfIssuer>SOME CO</nameOfIssuer><titleOfClass>NOTE 1.5%</titleOfClass><cusip>123456AB1</cusip>
    <value>300000</value><shrsOrPrnAmt><sshPrnamt>300000</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""


def test_parse_keeps_long_shares_only_and_sums_per_cusip():
    rows = thirteenf.parse_info_table(XML)
    assert list(rows) == ["67066G104"]  # the call option and the bond are dropped
    assert rows["67066G104"]["value"] == 1_500_000 and rows["67066G104"]["shares"] == 7_500


def test_is_stock():
    assert thirteenf._is_stock("NVIDIA CORPORATION", "COM", "Common Stock")
    assert not thirteenf._is_stock("SPDR S&P 500 ETF TR", "TR UNIT", "ETP")
    assert not thirteenf._is_stock("ISHARES TR", "CORE S&P500 ETF", None)  # name rules when OpenFIGI has nothing
    assert thirteenf._is_stock("TAIWAN SEMICONDUCTOR MFG LTD", "SPONSORED ADS", "Depositary Receipt")


def test_next_deadline_is_45_days_after_the_next_quarter():
    assert thirteenf._next_deadline(dt.date(2026, 6, 30)) == dt.date(2026, 11, 14)
    assert thirteenf._next_deadline(dt.date(2026, 12, 31)) == dt.date(2027, 5, 15)


def test_values_reported_in_thousands_are_scaled():
    rows = {"A": {"value": 892_310.0, "shares": 3_743_854.0}, "B": {"value": 493_140.0, "shares": 1_275_154.0}}
    thirteenf.fix_units(rows)
    assert rows["A"]["value"] == 892_310_000
    ok = {"A": {"value": 2_593_616_595.0, "shares": 10_882_003.0}}
    assert thirteenf.fix_units(ok)["A"]["value"] == 2_593_616_595.0


def test_amendments_restate_or_add(monkeypatch):
    tables = {"orig": {"A": {"issuer": "A", "title": None, "value": 100.0, "shares": 1.0}},
              "add": {"B": {"issuer": "B", "title": None, "value": 50.0, "shares": 1.0}},
              "full": {"A": {"issuer": "A", "title": None, "value": 100.0, "shares": 1.0},
                       "C": {"issuer": "C", "title": None, "value": 900.0, "shares": 9.0}}}
    monkeypatch.setattr(thirteenf, "_info_table", lambda c, cik, acc: acc)
    monkeypatch.setattr(thirteenf, "parse_info_table", lambda acc: {k: dict(v) for k, v in tables[acc].items()})
    monkeypatch.setattr(thirteenf, "_amendment_type", lambda c, cik, acc: "NEW HOLDINGS" if acc == "add" else "RESTATEMENT")
    docs = [{"accession": "orig", "form": "13F-HR"}, {"accession": "add", "form": "13F-HR/A"}]
    assert set(thirteenf._merged_table(None, 1, docs)) == {"A", "B"}
    docs.append({"accession": "full", "form": "13F-HR/A"})
    assert set(thirteenf._merged_table(None, 1, docs)) == {"A", "C"}  # a restatement replaces everything before it


def test_mislabelled_full_refiling_is_not_double_counted(monkeypatch):
    full = {"A": {"issuer": "A", "title": None, "value": 100.0, "shares": 1.0}, "B": {"issuer": "B", "title": None, "value": 50.0, "shares": 1.0}}
    monkeypatch.setattr(thirteenf, "_info_table", lambda c, cik, acc: acc)
    monkeypatch.setattr(thirteenf, "parse_info_table", lambda acc: {k: dict(v) for k, v in full.items()})
    monkeypatch.setattr(thirteenf, "_amendment_type", lambda c, cik, acc: "NEW HOLDINGS")
    rows = thirteenf._merged_table(None, 1, [{"accession": "o", "form": "13F-HR"}, {"accession": "a", "form": "13F-HR/A"}])
    assert rows["A"]["value"] == 100.0


def test_split_is_not_buying():
    class F:  # minimal Filing13F stand-in
        def __init__(self, i):
            self.id = i
    chosen = [[F(10 + m), F(20 + m)] for m in range(4)]
    hold = {}
    for m in range(4):  # a 10:1 split: everybody's shares x10, value about the same
        hold[10 + m] = {"X": ("X", None, 1000.0 + m, 100.0 * (m + 1)), "Y": ("Y", None, 500.0, 60.0)}
        hold[20 + m] = {"X": ("X", None, 990.0, 10.0 * (m + 1)), "Y": ("Y", None, 500.0, 50.0)}
    f = thirteenf._split_factors(chosen, hold)
    assert f == {"X": 10.0}  # Y's +20% is real buying, not a split
