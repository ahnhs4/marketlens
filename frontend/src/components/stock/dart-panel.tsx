"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDown, ExternalLink, FileText } from "lucide-react";
import { useState } from "react";

import { Pill, QueryState, ScoreMeter, Section, SkeletonBlock, UpdatedAt, scoreTone } from "@/components/common";
import { api } from "@/lib/api";
import { isNum } from "@/lib/format";

type N = number | null | undefined;
interface Growth { state: string; pct: number | null }
interface Item { key: string; label: string; value: N; unit: string; score: number; note: string | null; top_pct: N }
interface Pillar { label: string; weight: number; score: N; items: Item[] }
export interface DartMetrics {
  period: { year: number; reprt: string; end: string | null; months: number }; basis: "parent" | "total"; financial: boolean;
  revenue_ttm: N; operating_income_ttm: N; net_income_ttm: N; net_income_parent_ttm: N; equity: N; assets: N; liabilities: N;
  roe: N; roa: N; op_margin: N; debt_ratio: N; current_ratio: N; capital_impaired: boolean; rev_growth: Growth | null;
  op_growth: Growth | null; ni_growth: Growth | null; rev_cagr: N; rev_cagr_years: N; ocf_ttm?: N; fcf_ttm?: N; ocf_to_ni?: N;
  market_cap: N; pbr: N; per: N; psr: N; fcf_yield: N; dps?: N; div_yield?: N; payout?: N; dps_history?: N[];
}
export interface ValueupInfo { count: number; plans: number; last_plan: string | null; progress: number; pbr_plan: boolean; tags: string[];
  items: { time: string; title: string; url: string; kind: string }[] }
export interface LowPbr { name: string; market: string; published: string; pbr: number; gics_sector: string | null; pbr_date: string }
interface Dart {
  available: boolean; message?: string; name: string; financial: boolean; metrics: DartMetrics;
  score: { score: N; pillars: Record<string, Pillar>; missing: string[]; note?: string; weights_used?: Record<string, number> };
  summary: string[]; annual: Record<string, N>[]; quarters: { label: string; revenue: N; operating_income: N; net_income: N }[];
  low_pbr: LowPbr | null; valueup: ValueupInfo | null; peers: number; basis_note: string; source: string; updated_at: string;
  evidence: Evidence | null;
}
type EvStat = { ic: number; t: number | null; pos_years: number; years: number; q5_q1_pct: number };
export interface Evidence { years: [number, number]; n_years: number; total: EvStat | null; kospi: EvStat | null; kosdaq: EvStat | null;
  metrics: Record<string, EvStat>; method: string }

/** One line: how the score did in the Korean backtest. */
export function EvidenceLine({ ev, className }: { ev: Evidence | null | undefined; className?: string }) {
  if (!ev?.total) return null;
  const t = ev.total;
  return (
    <p className={cn("text-[11px] leading-4 text-muted-foreground", className)} title={ev.method}>
      과거 검증 {ev.years[0]}~{ev.years[1]}: 점수 상위 20%가 하위 20%보다 다음 1년 <b className="text-foreground">{t.q5_q1_pct >= 0 ? "+" : ""}{t.q5_q1_pct.toFixed(1)}%p</b> 더 올랐고,
      {" "}{Math.round(t.pos_years * t.years)}/{t.years}년 같은 방향 (순위상관 {t.ic.toFixed(2)})
    </p>
  );
}

export const useDart = (symbol: string, enabled = true) =>
  useQuery({ queryKey: ["dart", symbol], queryFn: () => api<Dart>(`/api/stock/${encodeURIComponent(symbol)}/dart`), staleTime: 3_600_000, enabled });

/** 1.2조원 / 3,450억원 */
export function won(v: N): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  return a >= 1e12 ? `${(v / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조` : a >= 1e8 ? `${Math.round(v / 1e8).toLocaleString("ko-KR")}억` : `${Math.round(v / 1e4).toLocaleString("ko-KR")}만`;
}
const pctTxt = (v: N, d = 1) => (isNum(v) ? `${v.toFixed(d)}%` : "—");
const xTxt = (v: N, d = 2) => (isNum(v) ? `${v.toFixed(d)}배` : "—");
const REPRT: Record<string, string> = { "11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "연간" };

function growthTxt(g: Growth | null) {
  if (!g) return "—";
  if (g.pct === null) return g.state;
  return `${g.pct >= 0 ? "+" : ""}${g.pct.toFixed(1)}%`;
}

/** Revenue (outer bar) and operating profit (inner bar, red when negative) per quarter. Banks and insurers file no
 *  revenue line, so for them the bars are operating profit and net profit. */
function QuarterBars({ rows }: { rows: Dart["quarters"] }) {
  const hasRev = rows.some((r) => isNum(r.revenue));
  const outerKey = hasRev ? "revenue" : "operating_income";
  const innerKey = hasRev ? "operating_income" : "net_income";
  const [outerLabel, innerLabel] = hasRev ? ["매출", "영업이익"] : ["영업이익", "순이익"];
  const qs = rows.filter((r) => isNum(r[outerKey]));
  if (qs.length < 2) return null;
  const max = Math.max(...qs.map((q) => Math.abs(q[outerKey] as number)), ...qs.map((q) => Math.abs((q[innerKey] as number) ?? 0)));
  const W = 640, H = 150, pad = 18, bw = (W - 8) / qs.length;
  const y = (v: number) => (H - pad) * (1 - v / max);
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H + 16}`} className="h-44 w-full" role="img" aria-label={`분기별 ${outerLabel}과 ${innerLabel}`}>
        {qs.map((q, i) => {
          const x = 4 + i * bw;
          const r = q[outerKey] as number, o = (q[innerKey] as number) ?? 0;
          return (
            <g key={q.label}>
              <title>{`${q.label} ${outerLabel} ${won(r)}원 · ${innerLabel} ${won(o)}원${hasRev && r ? ` (${((o / r) * 100).toFixed(1)}%)` : ""}`}</title>
              <rect x={x + bw * 0.12} y={r >= 0 ? y(r) : H - pad} width={bw * 0.76} height={Math.max(1, (H - pad) - y(Math.abs(r)))} rx={3}
                className={r >= 0 ? "fill-gold/25" : "fill-down/30"} />
              <rect x={x + bw * 0.3} y={o >= 0 ? y(o) : H - pad} width={bw * 0.4} height={Math.max(1.5, (H - pad) - y(Math.abs(o)))} rx={2}
                className={o >= 0 ? "fill-gold" : "fill-down"} opacity={o >= 0 ? 0.9 : 0.85} />
              <text x={x + bw / 2} y={H + 8} textAnchor="middle" className="fill-muted-foreground text-[10px]">{q.label}</text>
            </g>
          );
        })}
        <line x1={0} x2={W} y1={H - pad} y2={H - pad} className="stroke-border" />
      </svg>
      <figcaption className="flex flex-wrap gap-4 text-[11px] text-muted-foreground">
        <span><i className="mr-1 inline-block size-2 rounded-sm bg-gold/30 align-middle" />{outerLabel}</span>
        <span><i className="mr-1 inline-block size-2 rounded-sm bg-gold align-middle" />{innerLabel}</span>
        <span><i className="mr-1 inline-block size-2 rounded-sm bg-down align-middle" />손실 (아래로)</span>
        <span>Q4 = 연간 − 3분기 누적{hasRev ? "" : " · 금융업은 매출 대신 영업이익·순이익"}</span>
      </figcaption>
    </figure>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string | null; tone?: string }) {
  return (
    <div className="rounded-lg border bg-foreground/[0.02] px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("mt-0.5 text-[15px] font-semibold tabular", tone)}>{value}</div>
      {sub && <div className="text-[10.5px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function ValueupBadges({ low, vu }: { low: LowPbr | null | undefined; vu: ValueupInfo | null | undefined }) {
  if (!low && !vu) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {low && <Pill tone="down">거래소 저PBR 공표 · PBR {low.pbr}배 ({low.published})</Pill>}
      {vu?.last_plan && (
        <a href={vu.items.find((i) => i.kind === "plan")?.url ?? vu.items[0]?.url} target="_blank" rel="noopener noreferrer">
          <Pill tone="primary">기업가치 제고 계획 {vu.last_plan.slice(0, 10)}{vu.plans > 1 ? ` · ${vu.plans}건` : ""} <ExternalLink className="size-3" /></Pill>
        </a>
      )}
      {vu?.pbr_plan && <Pill>PBR 개선계획 제출</Pill>}
      {vu?.tags.includes("V100") && <Pill>코리아 밸류업 지수</Pill>}
    </div>
  );
}

/** Stock page, Korean companies: statements from OpenDART, the KR fundamental score and the value-up status. */
export function DartPanel({ symbol }: { symbol: string }) {
  const q = useDart(symbol);
  const [open, setOpen] = useState(false);
  const d = q.data;
  const m = d?.metrics;
  return (
    <Section title={<span className="flex items-center gap-2"><FileText className="size-4 text-gold" />재무제표 분석 · DART</span>}
      subtitle={d?.available && m ? `금융감독원 전자공시 · ${m.period.year}년 ${REPRT[m.period.reprt]} 보고서까지 반영 · ${d.basis_note}` : "금융감독원 전자공시(OpenDART) 재무제표"}
      right={<UpdatedAt at={d?.updated_at} />}>
      <QueryState q={q} label="DART 재무제표" skeleton={<SkeletonBlock rows={8} />}>
        {(d) => !d.available ? <p className="text-sm text-muted-foreground">{d.message ?? "데이터를 불러올 수 없습니다 (Data unavailable)"}</p> : (
          <div className="space-y-5">
            <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
              <div className="rounded-xl border border-gold/25 bg-gradient-to-br from-gold/[0.07] to-transparent p-4">
                <div className="text-[11px] tracking-[0.14em] text-gold">재무 점수</div>
                <div className={cn("mt-1 font-display text-5xl leading-none", scoreTone(d.score.score))}>{isNum(d.score.score) ? d.score.score.toFixed(0) : "—"}<span className="ml-1 text-base text-muted-foreground">/100</span></div>
                <p className="mt-2 text-[11px] leading-4 text-muted-foreground">국내 상장사 {d.peers.toLocaleString()}곳과 지표별로 비교한 백분위의 가중 평균</p>
                <EvidenceLine ev={d.evidence} className="mt-1.5" />
                <ul className="mt-3 space-y-1.5">
                  {Object.entries(d.score.pillars).map(([k, p]) => (
                    <li key={k} className="text-[12px]">
                      <div className="flex justify-between"><span>{p.label}</span><span className={cn("tabular", scoreTone(p.score))}>{isNum(p.score) ? p.score.toFixed(0) : "—"}</span></div>
                      <ScoreMeter score={p.score} />
                    </li>
                  ))}
                </ul>
              </div>
              <div className="min-w-0 space-y-3">
                <ValueupBadges low={d.low_pbr} vu={d.valueup} />
                <div className="space-y-1 text-[14px] leading-7 text-foreground/90">{d.summary.map((s, i) => <p key={i}>{s}</p>)}</div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
                  <Stat label="매출 (최근 4분기)" value={`${won(m!.revenue_ttm)}원`} sub={`누적 YoY ${growthTxt(m!.rev_growth)}`} />
                  <Stat label="영업이익 (최근 4분기)" value={`${won(m!.operating_income_ttm)}원`} sub={`누적 YoY ${growthTxt(m!.op_growth)}`}
                    tone={isNum(m!.operating_income_ttm) && m!.operating_income_ttm < 0 ? "text-down" : undefined} />
                  <Stat label="ROE" value={pctTxt(m!.roe)} sub={topTxt(d, "roe")} />
                  <Stat label={m!.financial ? "ROA" : "영업이익률"} value={pctTxt(m!.financial ? m!.roa : m!.op_margin)} sub={m!.financial ? "금융업" : topTxt(d, "op_margin")} />
                  <Stat label="PBR" value={xTxt(m!.pbr)} sub={d.low_pbr ? `거래소 산출 ${d.low_pbr.pbr}배` : topTxt(d, "pbr", true)} />
                  <Stat label="PER" value={xTxt(m!.per, 1)} sub={isNum(m!.per) ? topTxt(d, "per", true) : "적자 또는 데이터 없음"} />
                  {!m!.financial && <Stat label="부채비율" value={pctTxt(m!.debt_ratio, 0)} sub={`유동비율 ${pctTxt(m!.current_ratio, 0)}`} />}
                  <Stat label="영업현금흐름 (4분기)" value={isNum(m!.ocf_ttm) ? `${won(m!.ocf_ttm)}원` : "—"} sub={isNum(m!.ocf_to_ni) ? `순이익의 ${m!.ocf_to_ni.toFixed(1)}배` : null} />
                  <Stat label="잉여현금흐름 (4분기)" value={isNum(m!.fcf_ttm) ? `${won(m!.fcf_ttm)}원` : "—"} sub={isNum(m!.fcf_yield) ? `FCF 수익률 ${m!.fcf_yield.toFixed(1)}%` : null} />
                  <Stat label="주당배당금" value={isNum(m!.dps) ? `${m!.dps.toLocaleString()}원` : "—"} sub={isNum(m!.div_yield) ? `배당수익률 ${m!.div_yield.toFixed(2)}%${isNum(m!.payout) ? ` · 성향 ${m!.payout.toFixed(0)}%` : ""}` : null} />
                  <Stat label={`매출 ${m!.rev_cagr_years ?? 3}년 연평균`} value={pctTxt(m!.rev_cagr)} />
                  <Stat label="시가총액" value={`${won(m!.market_cap)}원`} sub="모든 주식 종류 합산" />
                </div>
              </div>
            </div>

            {d.quarters.length > 1 && (
              <div>
                <h3 className="mb-1 text-xs font-medium text-muted-foreground">분기 실적 (DART 주요계정 · 연결이 있으면 연결 기준)</h3>
                <QuarterBars rows={d.quarters} />
              </div>
            )}

            {d.annual.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-[12.5px] tabular">
                  <thead><tr className="text-left text-[11.5px] text-muted-foreground">
                    {["연도", "매출", "영업이익", "순이익", "영업이익률", "ROE", "부채비율", "영업현금흐름", "잉여현금흐름"].map((h, i) => <th key={h} className={cn("py-1.5 pr-3", i && "text-right")}>{h}</th>)}
                  </tr></thead>
                  <tbody className="divide-y divide-border/60">
                    {[...d.annual].reverse().map((a) => {
                      const ni = isNum(a.net_income_parent) ? a.net_income_parent : a.net_income;
                      return (
                        <tr key={a.year as number}>
                          <td className="py-1.5 pr-3 font-medium">{a.year}</td>
                          <td className="py-1.5 pr-3 text-right">{won(a.revenue)}</td>
                          <td className={cn("py-1.5 pr-3 text-right", isNum(a.operating_income) && a.operating_income < 0 && "text-down")}>{won(a.operating_income)}</td>
                          <td className={cn("py-1.5 pr-3 text-right", isNum(ni) && ni < 0 && "text-down")}>{won(ni)}</td>
                          <td className="py-1.5 pr-3 text-right">{pctTxt(a.op_margin)}</td>
                          <td className="py-1.5 pr-3 text-right">{pctTxt(a.roe)}</td>
                          <td className="py-1.5 pr-3 text-right">{pctTxt(a.debt_ratio, 0)}</td>
                          <td className="py-1.5 pr-3 text-right">{won(a.ocf)}</td>
                          <td className="py-1.5 text-right">{won(a.fcf)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="mt-1 text-[11px] text-muted-foreground">단위: 원 (조·억). 순이익·ROE는 지배주주 기준 데이터가 있으면 그 값을 씁니다. 현금흐름은 전체 재무제표를 받은 연도만 표시합니다.</p>
              </div>
            )}

            <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" aria-expanded={open}>
              <ChevronDown className={open ? "size-3.5 rotate-180" : "size-3.5"} />점수 계산 내역
            </button>
            {open && (
              <div className="space-y-3 text-xs">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {Object.entries(d.score.pillars).map(([k, p]) => (
                    <div key={k} className="rounded-lg border p-3">
                      <div className="mb-1.5 flex justify-between font-medium"><span>{p.label} <span className="font-normal text-muted-foreground">가중치 {Math.round(p.weight * 100)}%</span></span><span className={scoreTone(p.score)}>{isNum(p.score) ? p.score.toFixed(0) : "—"}</span></div>
                      {p.items.length ? (
                        <ul className="space-y-1">{p.items.map((i) => (
                          <li key={i.key} className="flex justify-between gap-2">
                            <span className="text-muted-foreground">{i.label}</span>
                            <span className="tabular">{i.note ?? (isNum(i.value) ? `${i.value.toFixed(i.unit === "배" ? 2 : 1)}${i.unit}` : "—")}
                              {isNum(i.top_pct) && <span className="ml-1 text-muted-foreground">상위 {i.top_pct.toFixed(0)}%</span>}
                              <b className={cn("ml-2", scoreTone(i.score))}>{i.score.toFixed(0)}</b></span>
                          </li>
                        ))}</ul>
                      ) : <p className="text-muted-foreground">해당 지표 없음</p>}
                    </div>
                  ))}
                </div>
                {d.score.missing.length > 0 && <p className="text-muted-foreground">제외된 지표: {d.score.missing.join(", ")}</p>}
                {d.score.note && <p className="text-muted-foreground">{d.score.note}</p>}
                {d.evidence && (
                  <div className="rounded-lg border p-3">
                    <div className="mb-1 font-medium">국내 백테스트 ({d.evidence.years[0]}~{d.evidence.years[1]}, 매년 5월 기준 다음 1년)</div>
                    <ul className="grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
                      {Object.entries(d.evidence.metrics).filter(([k]) => k in d.score.pillars || ["roe", "op_margin", "op_growth", "rev_growth", "pbr", "per", "debt_ratio"].includes(k)).map(([k, v]) => (
                        <li key={k} className="flex justify-between"><span className="text-muted-foreground">{d.score.pillars[k]?.label ?? EV_LABEL[k] ?? k}</span>
                          <span className="tabular">순위상관 {v.ic.toFixed(3)} · 상위−하위 {v.q5_q1_pct >= 0 ? "+" : ""}{v.q5_q1_pct.toFixed(1)}%p</span></li>
                      ))}
                    </ul>
                    <p className="mt-1.5 text-muted-foreground">{d.evidence.method}</p>
                  </div>
                )}
                <p className="text-muted-foreground">출처: {d.source}</p>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Section>
  );
}

const EV_LABEL: Record<string, string> = { roe: "ROE", op_margin: "영업이익률", op_growth: "영업이익 성장률", rev_growth: "매출 성장률",
  pbr: "PBR", per: "PER", debt_ratio: "부채비율" };

function topTxt(d: Dart, key: string, lowIsGood = false) {
  const it = Object.values(d.score.pillars).flatMap((p) => p.items).find((i) => i.key === key);
  if (!it || !isNum(it.top_pct)) return null;
  return lowIsGood ? `낮은 순 상위 ${(100 - it.top_pct).toFixed(0)}%` : `상위 ${it.top_pct.toFixed(0)}%`;
}
