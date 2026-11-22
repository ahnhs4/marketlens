"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";

import { Pill, QueryState, Section, SkeletonBlock } from "@/components/common";
import { api } from "@/lib/api";
import { compact, frac, isNum, num } from "@/lib/format";
import { pillarKo, regimeKo } from "@/lib/i18n";
import type { Num, Scores, Technical } from "@/lib/types";

interface FundDetail {
  fundamentals: Record<string, Num | string>;
  score: Scores["fundamental_detail"];
  pe_history: { current: number; median: number; min: number; max: number; percentile: number; years: number; basis: string; assessment: string; series: { date: string; pe: number }[] } | null;
  next_earnings: { date: string; eps_estimate: Num } | null;
  earnings_estimates: Record<string, Num> | null;
  earnings_history: { date: string; eps_estimate: Num; eps_actual: Num; surprise_pct: Num; beat: boolean | null }[];
  earnings_reaction: { beats: { n: number; avg_1d: number; avg_5d: number; positive_1d: number } | null; misses: { n: number; avg_1d: number; avg_5d: number; positive_1d: number } | null; all: { n: number } | null } | null;
  notes: string[];
}

const ROWS: [string, string, "pct" | "x" | "money"][][] = [
  [["pe", "PER", "x"], ["forward_pe", "선행 PER", "x"], ["peg", "PEG", "x"], ["ev_ebitda", "EV/EBITDA", "x"], ["ps", "PSR", "x"], ["fcf_yield", "FCF 수익률", "pct"]],
  [["revenue_growth", "매출 성장률 (전년比)", "pct"], ["earnings_growth", "EPS 성장률 (전년比)", "pct"], ["gross_margin", "매출총이익률", "pct"], ["operating_margin", "영업이익률", "pct"], ["roe", "ROE", "pct"], ["roa", "ROA", "pct"]],
  [["total_cash", "현금성 자산", "money"], ["total_debt", "총부채(차입금)", "money"], ["net_debt", "순부채", "money"], ["debt_to_equity", "부채비율 (D/E)", "x"], ["current_ratio", "유동비율", "x"], ["dividend_yield", "배당수익률", "x"]],
];

function PeSpark({ series, current, median }: { series: { date: string; pe: number }[]; current: number; median: number }) {
  const w = 300, h = 60;
  const v = series.map((s) => s.pe);
  const min = Math.min(...v), max = Math.max(...v);
  const y = (p: number) => h - ((p - min) / (max - min || 1)) * (h - 6) - 3;
  const pts = v.map((p, i) => `${(i / (v.length - 1)) * w},${y(p)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-16 w-full" role="img" aria-label={`PER 추이: 현재 ${current.toFixed(1)}, 중앙값 ${median.toFixed(1)}`}>
      <line x1={0} x2={w} y1={y(median)} y2={y(median)} className="stroke-muted-foreground/40" strokeWidth={1} />
      <polyline points={pts} fill="none" className="stroke-s1" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      <circle cx={w} cy={y(current)} r={3} className="fill-s1 stroke-card" strokeWidth={2} />
    </svg>
  );
}

export function FundamentalsPanel({ symbol, currency }: { symbol: string; currency?: string | null }) {
  const q = useQuery({ queryKey: ["fund", symbol], queryFn: () => api<FundDetail>(`/api/stock/${encodeURIComponent(symbol)}/fundamentals`), staleTime: 3_600_000 });
  return (
    <Section title="펀더멘털 · 실적" subtitle="Yahoo Finance 재무 데이터 · 실적 발표 EPS">
      <QueryState q={q} label="펀더멘털" skeleton={<SkeletonBlock rows={6} />}>
        {(d) => (
          <div className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-3">
              {ROWS.map((group, gi) => (
                <dl key={gi} className="space-y-1 text-xs">
                  <div className="mb-1 font-semibold text-muted-foreground">{["밸류에이션", "성장성 · 수익성", "재무 상태"][gi]}</div>
                  {group.map(([k, label, kind]) => {
                    const v = d.fundamentals[k];
                    const txt = !isNum(v) ? "—" : kind === "pct" ? frac(v) : kind === "money" ? compact(v, currency) : k === "dividend_yield" ? `${num(v, 2)}%` : num(v, 2);
                    return <div key={k} className="flex justify-between border-b border-dashed border-border/60 pb-0.5"><dt className="text-muted-foreground">{label}</dt><dd className="tabular">{txt}</dd></div>;
                  })}
                </dl>
              ))}
            </div>
            {d.pe_history ? (
              <div className="rounded-lg border p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">PER: 최근 {d.pe_history.years}년 자체 추이 대비</span>
                  <Pill tone={d.pe_history.percentile >= 75 ? "down" : d.pe_history.percentile <= 25 ? "up" : "default"}>{d.pe_history.assessment}</Pill>
                </div>
                <div className="mt-1 grid grid-cols-4 gap-2 tabular">
                  <span><span className="text-muted-foreground">현재 </span>{d.pe_history.current.toFixed(1)}</span>
                  <span><span className="text-muted-foreground">중앙값 </span>{d.pe_history.median.toFixed(1)}</span>
                  <span><span className="text-muted-foreground">백분위 </span>{d.pe_history.percentile.toFixed(0)}%</span>
                  <span><span className="text-muted-foreground">범위 </span>{d.pe_history.min.toFixed(0)}–{d.pe_history.max.toFixed(0)}</span>
                </div>
                <PeSpark series={d.pe_history.series} current={d.pe_history.current} median={d.pe_history.median} />
                <p className="text-[11px] text-muted-foreground">기준: {d.pe_history.basis}. 회색 선 = 중앙값.</p>
              </div>
            ) : <p className="text-xs text-muted-foreground">과거 PER 백분위를 계산할 수 없습니다 (EPS가 양수인 분기 실적 8개 이상 필요).</p>}
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="text-xs">
                <div className="mb-1 font-medium">실적: EPS 실제치 vs 예상치</div>
                {d.next_earnings && <p className="mb-1">다음 실적 발표 <b>{new Date(d.next_earnings.date).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" })}</b>{isNum(d.next_earnings.eps_estimate) ? ` · EPS 예상 ${d.next_earnings.eps_estimate.toFixed(2)}` : ""}</p>}
                <table className="w-full tabular">
                  <thead><tr className="text-left text-muted-foreground"><th className="font-normal">날짜</th><th className="font-normal">예상</th><th className="font-normal">실제</th><th className="font-normal">서프라이즈</th><th className="font-normal">결과</th></tr></thead>
                  <tbody>{d.earnings_history.slice(0, 8).map((e) => (
                    <tr key={e.date} className="border-t">
                      <td className="py-1">{e.date.slice(0, 10)}</td><td>{num(e.eps_estimate)}</td><td>{num(e.eps_actual)}</td>
                      <td className={cn(isNum(e.surprise_pct) && e.surprise_pct >= 0 ? "text-up" : "text-down")}>{isNum(e.surprise_pct) ? `${e.surprise_pct > 0 ? "+" : ""}${e.surprise_pct.toFixed(1)}%` : "—"}</td>
                      <td>{e.beat === null ? "—" : e.beat ? <span className="text-up">상회</span> : <span className="text-down">하회</span>}</td>
                    </tr>))}</tbody>
                </table>
              </div>
              <div className="text-xs">
                <div className="mb-1 font-medium">실적 발표 후 주가 반응 (과거)</div>
                {d.earnings_reaction ? (
                  <table className="w-full tabular">
                    <thead><tr className="text-left text-muted-foreground"><th className="font-normal">결과</th><th className="font-normal">횟수</th><th className="font-normal">평균 1일</th><th className="font-normal">평균 5일</th><th className="font-normal">1일 상승 확률</th></tr></thead>
                    <tbody>{(["beats", "misses"] as const).map((k) => { const r = d.earnings_reaction![k]; return r && (
                      <tr key={k} className="border-t"><td className="py-1">{k === "beats" ? "예상 상회" : "예상 하회"}</td><td>{r.n}</td><td>{frac(r.avg_1d)}</td><td>{frac(r.avg_5d)}</td><td>{(r.positive_1d * 100).toFixed(0)}%</td></tr>); })}</tbody>
                  </table>
                ) : <p className="text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable)</p>}
                <ul className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">{d.notes.map((n) => <li key={n}>· {n}</li>)}</ul>
              </div>
            </div>
          </div>
        )}
      </QueryState>
    </Section>
  );
}

const QUALITY_KO: Record<string, string> = {
  empty: "데이터 없음", timezone: "시간대 추정", duplicate_timestamp: "중복 시각", non_positive_price: "비정상 가격",
  negative_volume: "음수 거래량", ohlc_inconsistent: "OHLC 불일치", possible_unadjusted_split: "액면분할 미반영 의심",
  extreme_move: "극단적 가격 변동", missing_sessions: "누락 거래일", non_session_bars: "휴장일 데이터", stale_prices: "가격 정체",
};

export function BullBear({ tech, scores }: { tech?: Technical; scores?: Scores }) {
  if (!tech) return null;
  const bull = [...tech.signal.bullish_factors.slice(0, 4), ...(scores?.macro_detail?.reasons ?? []).slice(0, 2)];
  const pillars = scores?.fundamental_detail?.pillars ?? {};
  for (const [k, v] of Object.entries(pillars)) if (isNum(v.score) && v.score >= 70) bull.push(`${pillarKo(k)} 우수 (${v.score.toFixed(0)}/100)`);
  const bear = [...tech.signal.bearish_factors.slice(0, 4), ...(scores?.macro_detail?.risks ?? []).slice(0, 2)];
  for (const [k, v] of Object.entries(pillars)) if (isNum(v.score) && v.score <= 40) bear.push(`${pillarKo(k)} 취약 (${v.score.toFixed(0)}/100)`);
  const risks: string[] = [];
  const ind = tech.indicators;
  if (isNum(ind.vol20)) risks.push(`실현 변동성 연율 ${(ind.vol20 * 100).toFixed(0)}%`);
  if (isNum(ind.atr_pct)) risks.push(`일평균 변동폭(ATR) 주가의 ${ind.atr_pct.toFixed(1)}%`);
  const q = tech.data?.quality;
  if (q && q.issues.length) risks.push(`데이터 품질 경고: ${q.issues.map((i) => QUALITY_KO[i.check] ?? i.check).join(", ")}`);
  if (tech.regime === "Risk-Off") risks.push(`시장 국면이 ${regimeKo(tech.regime)}`);
  return (
    <Section title="강세 시나리오 / 약세 시나리오" subtitle="위 신호들만 모아 정리 · 추가 가정 없음">
      <div className="grid gap-4 md:grid-cols-3">
        <div><h3 className="mb-1.5 text-xs font-semibold uppercase text-up">강세 근거</h3><ul className="space-y-1 text-sm">{bull.map((b, i) => <li key={i}>· {b}</li>)}</ul></div>
        <div><h3 className="mb-1.5 text-xs font-semibold uppercase text-down">약세 근거</h3><ul className="space-y-1 text-sm">{bear.length ? bear.map((b, i) => <li key={i}>· {b}</li>) : <li className="text-muted-foreground">약세 기술적 요인 없음</li>}</ul></div>
        <div><h3 className="mb-1.5 text-xs font-semibold uppercase text-warn">위험 요인</h3><ul className="space-y-1 text-sm">{risks.map((b, i) => <li key={i}>· {b}</li>)}</ul></div>
      </div>
    </Section>
  );
}
