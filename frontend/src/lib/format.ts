// Number / time formatting. Times are stored and transported in UTC and rendered in
// Korea time (KST) by default, with US Eastern time alongside where it matters.

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function num(v: unknown, digits = 2): string {
  if (!isNum(v)) return "—";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function price(v: unknown, currency?: string | null): string {
  if (!isNum(v)) return "—";
  if (currency === "KRW" || currency === "JPY") return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  const d = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function currencySymbol(c?: string | null) {
  return c === "USD" ? "$" : c === "KRW" ? "₩" : c === "JPY" ? "¥" : c === "EUR" ? "€" : c === "HKD" ? "HK$" : "";
}

export function pct(v: unknown, digits = 2, signed = true): string {
  if (!isNum(v)) return "—";
  const s = v.toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

/** v is a fraction (0.123 -> +12.3%). */
export function frac(v: unknown, digits = 1, signed = true): string {
  return isNum(v) ? pct(v * 100, digits, signed) : "—";
}

export function compact(v: unknown, currency?: string | null): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const sym = currencySymbol(currency);
  if (currency === "KRW") {
    if (a >= 1e12) return `₩${(v / 1e12).toFixed(1)}조`;
    if (a >= 1e8) return `₩${(v / 1e8).toFixed(0)}억`;
  }
  if (a >= 1e12) return `${sym}${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sym}${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${sym}${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${sym}${(v / 1e3).toFixed(1)}K`;
  return `${sym}${v.toFixed(0)}`;
}

/** Whole won with separators: 964653.97 -> "+964,654원". */
export function won(v: unknown, signed = false): string {
  if (!isNum(v)) return "—";
  return `${signed && v > 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}원`;
}

const kst = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
const et = new Intl.DateTimeFormat("ko-KR", { timeZone: "America/New_York", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

export function timeKST(iso?: string | number | null): string {
  if (!iso) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${kst.format(d)} (한국)`;
}

export function timeET(iso?: string | number | null): string {
  if (!iso) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : `${et.format(d)} (뉴욕)`;
}

export function ago(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return "—";
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 60) return "방금 전";
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

export function signClass(v: unknown) {
  if (!isNum(v) || v === 0) return "text-muted-foreground";
  return v > 0 ? "text-up" : "text-down";
}

export function dateKo(iso?: string | number | Date | null): string {
  if (!iso) return "—";
  const d = iso instanceof Date ? iso : typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" });
}
