// Korean display names for identifiers that the API keeps in English (stable keys).

const pick = (m: Record<string, string>) => (k?: string | null) => (k ? m[k] ?? k : "—");

export const signalKo = pick({ "STRONG BUY": "강력 매수", BUY: "매수", NEUTRAL: "중립", SELL: "매도", "STRONG SELL": "강력 매도" });
export const ratingKo = pick({ "Strong Buy": "강력 매수", Buy: "매수", Neutral: "중립", Sell: "매도", "Strong Sell": "강력 매도" });
export const viewKo = pick({ Bullish: "강세", Neutral: "중립", Bearish: "약세" });
export const regimeKo = pick({ "Risk-On": "위험선호", Neutral: "중립", "Risk-Off": "위험회피" });
export const quadrantKo = pick({
  "Inflationary Growth": "인플레이션 동반 성장", "Disinflationary Growth": "디스인플레이션 성장(골디락스)",
  Stagflation: "스태그플레이션", Recessionary: "경기 침체형",
});
export const overlayKo = pick({
  "Rate-Cut Expectation": "금리인하 기대", "Rate-Hike Expectation": "금리인상 기대",
  "Liquidity-Driven": "유동성 장세", "Recession Warning": "경기침체 경고",
});
export const bandKo = pick({
  Exceptional: "매우 우수", Strong: "강함", Positive: "긍정적", "Moderately Positive": "다소 긍정적",
  Neutral: "중립", Weak: "약함", "Very Weak": "매우 약함", "Highly Risky": "고위험",
});
export const patternKo = pick({
  Breakout: "상향 돌파", Breakdown: "하향 이탈", "Double Bottom": "이중 바닥", "Double Top": "이중 천장",
  "Head & Shoulders": "헤드앤숄더", "Inverse Head & Shoulders": "역헤드앤숄더", "Ascending Triangle": "상승 삼각형",
  "Descending Triangle": "하락 삼각형", "Symmetrical Triangle": "대칭 삼각형", "Bull Flag": "상승 깃발형",
  "Bear Flag": "하락 깃발형", Pennant: "상승 페넌트", "Bear Pennant": "하락 페넌트", "Cup & Handle": "컵앤핸들",
  Range: "박스권", "Trend Reversal (bullish)": "상승 추세 전환", "Trend Reversal (bearish)": "하락 추세 전환",
});
export const statusKo = pick({ confirmed: "확인됨", forming: "형성 중" });
export const directionKo = pick({ bullish: "상승", bearish: "하락", neutral: "중립" });
export const categoryKo = pick({
  Fed: "연준", Inflation: "인플레이션", Treasury: "국채", Geopolitics: "지정학", Oil: "유가", AI: "AI",
  Semiconductors: "반도체", China: "중국", Japan: "일본", Korea: "한국", Europe: "유럽", "Economic Data": "경제지표",
  Earnings: "실적", Guidance: "가이던스", "Analyst Action": "애널리스트", Product: "제품", Partnership: "제휴",
  "M&A": "M&A", "Regulation/Legal": "규제·소송", "SEC Filing": "공시", Corporate: "기업", Crypto: "가상자산", Other: "기타",
});
export const sentimentKo = pick({ "Very Bullish": "매우 긍정", Bullish: "긍정", Neutral: "중립", Bearish: "부정", "Very Bearish": "매우 부정" });
export const impactKo = pick({ Low: "낮음", Medium: "보통", High: "높음", Critical: "매우 높음" });
export const levelKo = pick({ High: "높음", Medium: "보통", Low: "낮음" });
export const groupKo = pick({
  Technology: "기술", Semiconductors: "반도체", Software: "소프트웨어", "Communication Services": "커뮤니케이션",
  "Consumer Discretionary": "경기소비재", "Consumer Staples": "필수소비재", "Health Care": "헬스케어", Biotech: "바이오",
  Financials: "금융", "Regional Banks": "지역은행", Industrials: "산업재", "Aerospace & Defense": "항공·방산",
  Homebuilders: "주택건설", Materials: "소재", Energy: "에너지", Utilities: "유틸리티", "Real Estate": "부동산",
  "Gold Miners": "금광", Gold: "금", "United States": "미국", Korea: "한국", Japan: "일본", China: "중국",
  Eurozone: "유로존", Taiwan: "대만", India: "인도", "Long Treasuries": "미국 장기국채", "US Dollar": "미 달러",
  "Crude Oil": "원유", Bitcoin: "비트코인", "High Yield Credit": "하이일드 채권", Unknown: "기타",
  "Financial Services": "금융", Healthcare: "헬스케어", "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재",
  "Basic Materials": "소재",
});
export const kindKo = pick({ Sector: "섹터", Industry: "산업", Country: "국가", Asset: "자산" });
export const trendKo = pick({ Uptrend: "상승 추세", Downtrend: "하락 추세", Sideways: "횡보", "n/a": "—" });
export const structureKo = pick({
  "Strong Bullish Structure": "강한 정배열", "Bullish Structure": "정배열 우위", "Mixed Structure": "혼조 배열",
  "Bearish Structure": "역배열 우위", "Strong Bearish Structure": "강한 역배열",
});
export const breadthKo = pick({ Healthy: "양호", Neutral: "중립", Weak: "약함" });
export const zoneKo = pick({ oversold: "과매도", bearish: "약세 구간", bullish: "강세 구간", overbought: "과매수" });
export const macroGroupKo = pick({
  Rates: "금리", Inflation: "물가", Currencies: "환율", Commodities: "원자재", Liquidity: "유동성", Economy: "경기", Sentiment: "투자심리",
});
export const componentKo = pick({
  trend: "추세", momentum: "모멘텀", rsi: "RSI", volume: "거래량", breakout: "돌파",
  support_resistance: "지지/저항", volatility: "변동성", pattern: "패턴", timing: "진입 타이밍",
});
export const pillarKo = pick({ valuation: "밸류에이션", growth: "성장성", quality: "수익성", balance_sheet: "재무 건전성",
  profitability: "수익성", stability: "재무 안정성", cash_return: "현금흐름·주주환원" });
export const universeKo = pick({ ALL: "전체", SP500: "S&P500", NDX: "나스닥100*", KOSPI: "코스피", KOSDAQ: "코스닥", WATCHLIST: "관심종목" });
export const scanStateKo = pick({ idle: "대기", running: "진행 중", done: "완료", error: "오류" });
export const marketStateKo = pick({
  REGULAR: "장중", PRE: "프리마켓", PREPRE: "장 마감", POST: "애프터마켓", POSTPOST: "장 마감", CLOSED: "장 마감",
});
export const sessionKo = pick({ open: "장중", pre: "프리마켓", post: "애프터마켓", closed: "장 마감" });

/** Scan rows carry "Bull Flag (forming)"-style strings; translate both parts. */
export function patternStatusKo(v?: string | null) {
  if (!v) return "—";
  const m = /^(.*) \((confirmed|forming)\)$/.exec(v);
  return m ? `${patternKo(m[1])} (${statusKo(m[2])})` : patternKo(v);
}

const FORM_KO: Record<string, string> = {
  "10-Q": "분기보고서", "10-K": "연차보고서", "8-K": "수시공시", "6-K": "해외기업 수시공시", "20-F": "해외기업 연차보고서",
  "S-1": "증권신고서(IPO)", "S-3": "증권신고서", "DEF 14A": "주총 위임장", "SC 13D": "대량보유 보고", "SC 13G": "대량보유 보고", "424B5": "증권 발행 설명서",
};
const ITEM_KO: Record<string, string> = {
  "Material agreement": "주요 계약", "Acquisition/disposition": "인수·처분", "Results of operations (earnings)": "실적 발표",
  "Restructuring costs": "구조조정 비용", Impairment: "자산 손상", "Officer/director change": "임원·이사 변경",
  "Reg FD disclosure": "공정공시(Reg FD)", "Other events": "기타 사항", "Cybersecurity incident": "사이버보안 사고",
  "Unregistered equity sale": "미등록 지분 매각", "Shareholder vote": "주주총회 의결",
};
/** SEC titles arrive as "8-K filed: Other events, ..." (English keeps the rule classifier stable). */
export function filingKo(title: string) {
  const m = /^(.+?) filed(?:: (.*))?$/.exec(title);
  if (!m) return title;
  const form = m[1], rest = m[2];
  const head = FORM_KO[form] ? `${form} ${FORM_KO[form]}` : `${form} 공시`;
  if (!rest) return head;
  return `${head}: ${rest.split(", ").map((x) => ITEM_KO[x] ?? x).join(", ")}`;
}
