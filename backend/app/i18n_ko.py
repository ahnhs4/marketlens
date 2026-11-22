"""Korean display names for identifiers that stay English inside the data model.

Identifiers (signal labels, pattern names, regime labels...) remain stable English keys so
logic, storage and tests do not depend on wording; every sentence shown to users is built
with these Korean names.
"""
from __future__ import annotations

PATTERN_KO = {
    "Breakout": "상향 돌파", "Breakdown": "하향 이탈", "Double Bottom": "이중 바닥", "Double Top": "이중 천장",
    "Head & Shoulders": "헤드앤숄더", "Inverse Head & Shoulders": "역헤드앤숄더",
    "Ascending Triangle": "상승 삼각형", "Descending Triangle": "하락 삼각형", "Symmetrical Triangle": "대칭 삼각형",
    "Bull Flag": "상승 깃발형", "Bear Flag": "하락 깃발형", "Pennant": "상승 페넌트", "Bear Pennant": "하락 페넌트",
    "Cup & Handle": "컵앤핸들", "Range": "박스권", "Trend Reversal (bullish)": "상승 추세 전환",
    "Trend Reversal (bearish)": "하락 추세 전환",
}
STATUS_KO = {"confirmed": "확인됨", "forming": "형성 중"}
ZONE_KO = {"oversold": "과매도", "bearish": "약세 구간", "bullish": "강세 구간", "overbought": "과매수"}
STRUCTURE_KO = {
    "Strong Bullish Structure": "강한 정배열", "Bullish Structure": "정배열 우위", "Mixed Structure": "혼조 배열",
    "Bearish Structure": "역배열 우위", "Strong Bearish Structure": "강한 역배열",
}
REGIME_KO = {"Risk-On": "위험선호(Risk-On)", "Neutral": "중립", "Risk-Off": "위험회피(Risk-Off)"}
SIGNAL_KO = {"STRONG BUY": "강력 매수", "BUY": "매수", "NEUTRAL": "중립", "SELL": "매도", "STRONG SELL": "강력 매도"}
GROUP_KO = {
    "Technology": "기술", "Semiconductors": "반도체", "Software": "소프트웨어", "Communication Services": "커뮤니케이션",
    "Consumer Discretionary": "경기소비재", "Consumer Staples": "필수소비재", "Health Care": "헬스케어", "Biotech": "바이오",
    "Financials": "금융", "Regional Banks": "지역은행", "Industrials": "산업재", "Aerospace & Defense": "항공·방산",
    "Homebuilders": "주택건설", "Materials": "소재", "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산",
    "Gold Miners": "금광", "Gold": "금", "United States": "미국", "Korea": "한국", "Japan": "일본", "China": "중국",
    "Eurozone": "유로존", "Taiwan": "대만", "India": "인도", "Long Treasuries": "미국 장기국채", "US Dollar": "미 달러",
    "Crude Oil": "원유", "Bitcoin": "비트코인", "High Yield Credit": "하이일드 채권",
}


def pattern(name: str) -> str:
    return PATTERN_KO.get(name, name)


def group(name: str | None) -> str:
    return GROUP_KO.get(name or "", name or "")

CATEGORY_KO = {
    "Fed": "연준", "Inflation": "인플레이션", "Treasury": "국채", "Geopolitics": "지정학", "Oil": "유가", "AI": "AI",
    "Semiconductors": "반도체", "China": "중국", "Japan": "일본", "Korea": "한국", "Europe": "유럽",
    "Economic Data": "경제지표", "Earnings": "실적", "Guidance": "가이던스", "Analyst Action": "애널리스트",
    "Product": "제품", "Partnership": "제휴", "M&A": "M&A", "Regulation/Legal": "규제·소송", "SEC Filing": "공시",
    "Corporate": "기업", "Crypto": "가상자산", "Other": "기타",
}
EVENT_KO = {
    "earnings_beat": "실적 상회", "earnings_miss": "실적 하회", "guidance_raise": "가이던스 상향", "guidance_cut": "가이던스 하향",
    "analyst_upgrade": "투자의견 상향", "analyst_downgrade": "투자의견 하향", "price_target_change": "목표주가 변경",
    "acquisition": "인수·합병", "lawsuit": "소송", "regulatory_action": "규제 조치", "central_bank_signal": "중앙은행 발언",
    "macro_release": "경제지표 발표", "product_launch": "제품 출시", "partnership": "제휴", "filing": "공시",
    "opinion": "의견·칼럼", "other": "기타",
}
COMPONENT_KO = {"trend": "추세", "momentum": "모멘텀", "rsi": "RSI", "volume": "거래량", "breakout": "돌파",
                "support_resistance": "지지/저항", "volatility": "변동성", "pattern": "패턴",
                "timing": "진입 타이밍"}
