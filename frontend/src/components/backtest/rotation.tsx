"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Play } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { LineChart } from "@/components/charts/line-chart";
import { Pill, Section, Segmented, SkeletonBlock, Unavailable, UpdatedAt } from "@/components/common";
import { api, post } from "@/lib/api";
import { frac, isNum, num, won } from "@/lib/format";
import { STATIC } from "@/lib/static";

import { BTN, BTN_GHOST, COLORS, type Curve, INPUT } from "./shared";

// Monthly rotation: hold the top N of the ranking, replace them at each month end, after commission, slippage and tax.
// The backend rebuilds each month's ranking from data up to that day only (see the notes it returns).

type Universe = "SP500" | "KOSPI";
interface RotMetrics { total_return: number; cagr: number; volatility: number; sharpe: number | null; sortino: number | null; max_drawdown: number; start: string; end: string }
interface Pick { symbol: string; name: string; score: number }
interface Rebalance { signal_date: string; trade_date: string; scored: number; excluded: number; picks: Pick[]; return: number | null; open: boolean }
interface Job { state: "idle" | "running" | "done" | "error"; progress: number; message: string | null }
interface RotResult {
  status: "ready" | "needs_prepare" | "insufficient"; message?: string; job?: Job; missing_months?: number; members?: number;
  universe: Universe; universe_name: string; top_n: number;
  params: { commission_bps: number; slippage_bps: number; sell_tax_bps: number; gains_tax: boolean; capital: number };
  metrics: Record<string, RotMetrics | null>; names: Record<string, string>; equity: Record<string, Curve>;
  yearly: ({ year: number } & Record<string, number | null>)[];
  costs: { commission: number; slippage: number; sell_tax: number; gains_tax: number; tax_accrued: number; total: number; drag: number | null };
  avg_turnover: number | null; rebalances: Rebalance[]; months: number; current: { trade_date: string; picks: Pick[] };
  data: { members: number; start: string; end: string; scores_updated_at: string | null }; notes: string[]; updated_at: string; disclaimer: string;
}

const ORDER = ["strategy", "gross", "hold_first", "equal_weight", "sp500", "kospi"];
const TOPS = ["5", "10", "20"].map((v) => ({ value: v, label: `상위 ${v}` }));
const BLANK = { commission_bps: "", slippage_bps: "", sell_tax_bps: "", gains_tax: "", capital: "" };
const man = (v: number) => `${Math.round(v / 1e4).toLocaleString("ko-KR")}만원`;

export function Rotation() {
  const qc = useQueryClient();
  const [u, setU] = useState<Universe>("SP500");
  const [top, setTop] = useState("10");
  const [draft, setDraft] = useState(BLANK);
  const [cost, setCost] = useState(BLANK);
  const extra = Object.entries(cost).filter(([, v]) => v !== "").map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join("");
  const path = `/api/rotation?universe=${u}&top_n=${top}${extra}`;
  const q = useQuery({ queryKey: ["rotation", path], queryFn: () => api<RotResult>(path), staleTime: 10 * 60_000 });
  const prepare = useMutation({ mutationFn: () => post<Job>(`/api/rotation/prepare?universe=${u}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["rotation-status", u] }) });
  const waiting = q.data?.status === "needs_prepare" && !STATIC;
  const job = useQuery({
    queryKey: ["rotation-status", u], queryFn: () => api<Job>(`/api/rotation/status?universe=${u}`), enabled: waiting,
    refetchInterval: (s) => (s.state.data?.state === "running" ? 3000 : false),
  });
  const jobState = job.data?.state;
  useEffect(() => { if (jobState === "done") qc.invalidateQueries({ queryKey: ["rotation"] }); }, [jobState, qc]);

  const d = q.data;
  const s = d?.metrics?.strategy;
  const bench = d ? d.metrics[u === "SP500" ? "sp500" : "kospi"] : null;
  const keys = d?.status === "ready" ? ORDER.filter((k) => d.metrics[k]) : [];
  return (
    <div className="space-y-4">
      <Section title="순위표 상위 종목 매달 교체" subtitle="매달 말 순위를 다시 매겨 상위 N개를 같은 비중으로 보유하고, 다음 거래일 시가에 갈아탑니다. 수수료 · 슬리피지 · 세금을 뺀 결과입니다."
        right={d?.status === "ready" ? <UpdatedAt at={d.updated_at} /> : undefined}>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented ariaLabel="유니버스" value={u} onChange={setU} options={[{ value: "SP500", label: "S&P 500" }, { value: "KOSPI", label: "코스피 상위 100" }]} />
          <Segmented ariaLabel="보유 종목 수" value={top} onChange={setTop} options={TOPS} />
          {d?.status === "ready" && <span className="text-xs text-muted-foreground">{d.data.start} → {d.data.end} · {d.months}개월 · {d.data.members}개 종목</span>}
        </div>
        {!STATIC && (
          <details className="mt-3 text-xs">
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">비용 · 세금 가정 바꾸기{d?.status === "ready" && ` (지금: 수수료 ${num(d.params.commission_bps, 1)}bp · 슬리피지 ${num(d.params.slippage_bps, 1)}bp · 거래세 ${num(d.params.sell_tax_bps, 0)}bp · 양도세 ${d.params.gains_tax ? "포함" : "없음"} · 초기자금 ${man(d.params.capital)})`}</summary>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              {([["commission_bps", "수수료 (bp)"], ["slippage_bps", "슬리피지 (bp)"], ["sell_tax_bps", "매도 거래세 (bp)"], ["capital", "초기자금 (원)"]] as const).map(([k, label]) => (
                <label key={k} className="text-[11px] text-muted-foreground">{label}
                  <input type="number" inputMode="decimal" min={0} placeholder="기본값" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} className={cn(INPUT, "mt-1 block h-7 text-xs tabular text-foreground", k === "capital" ? "w-32" : "w-24")} />
                </label>
              ))}
              <label className="text-[11px] text-muted-foreground">해외주식 양도세 22%
                <select value={draft.gains_tax} onChange={(e) => setDraft({ ...draft, gains_tax: e.target.value })} className={cn(INPUT, "mt-1 block h-7 text-xs text-foreground")}>
                  <option value="">시장 기본값</option><option value="true">포함</option><option value="false">제외</option>
                </select>
              </label>
              <button onClick={() => setCost(draft)} className={cn(BTN, "h-7 py-0 text-xs")}><Play className="size-3.5" />다시 계산</button>
              <button onClick={() => { setDraft(BLANK); setCost(BLANK); }} className={cn(BTN_GHOST, "h-7 py-0")}>기본값으로</button>
            </div>
          </details>
        )}
      </Section>

      {q.isLoading ? <Section title="결과"><SkeletonBlock rows={8} /></Section> : q.isError || !d ? <Unavailable message={(q.error as Error | undefined)?.message} onRetry={() => q.refetch()} label="교체 전략 백테스트" /> : d.status === "needs_prepare" ? (
        <Section title="과거 순위 계산이 필요합니다">
          <p className="text-sm leading-relaxed">{d.message}</p>
          {STATIC ? <p className="mt-2 text-xs text-muted-foreground">데모에는 이 조합의 결과가 저장돼 있지 않습니다.</p> : jobState === "running" ? (
            <div className="mt-3 max-w-md">
              <div className="h-1.5 overflow-hidden rounded-full bg-foreground/[0.07]"><div className="h-full rounded-full bg-gold transition-all" style={{ width: `${Math.round((job.data?.progress ?? 0) * 100)}%` }} /></div>
              <p className="mt-1.5 text-xs text-muted-foreground">{job.data?.message} · {Math.round((job.data?.progress ?? 0) * 100)}%</p>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button onClick={() => prepare.mutate()} disabled={prepare.isPending} className={BTN}><Play className="size-4" />과거 순위 계산 시작</button>
              {jobState === "error" && <span className="text-xs text-down">{job.data?.message}</span>}
              <span className="text-xs text-muted-foreground">종목 수에 따라 몇 분 걸립니다. 한 번 계산하면 저장됩니다.</span>
            </div>
          )}
        </Section>
      ) : d.status !== "ready" ? <Unavailable message={d.message} label="교체 전략 백테스트" /> : (
        <>
          <Section title={`${d.universe_name} · ${d.names.strategy}`}>
            {s && bench && (
              <p className="mb-4 rounded-lg border bg-muted/20 px-3.5 py-2.5 text-sm leading-relaxed">
                비용 · 세금을 빼고 연 <b className={s.cagr >= 0 ? "text-up" : "text-down"}>{frac(s.cagr)}</b>, 같은 기간 {d.names[u === "SP500" ? "sp500" : "kospi"]}는 연 {frac(bench.cagr)}
                {" "}(<span className={s.cagr - bench.cagr >= 0 ? "text-up" : "text-down"}>{frac(s.cagr - bench.cagr)}p</span>). 대신 최대 낙폭은 <b className="text-down">{frac(s.max_drawdown)}</b>로 지수({frac(bench.max_drawdown)})보다 {s.max_drawdown < bench.max_drawdown ? "깊었습니다" : "얕았습니다"}.
                {isNum(d.costs.drag) && <> 비용과 세금이 연 {frac(d.costs.drag, 1, false)}p를 가져갔습니다.</>}
              </p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-xs tabular">
                <thead><tr className="text-left text-muted-foreground"><th className="font-normal">구분</th><th className="font-normal">누적 수익률</th><th className="font-normal">연평균 (CAGR)</th><th className="font-normal">최대 낙폭</th><th className="font-normal">변동성</th><th className="font-normal">샤프</th></tr></thead>
                <tbody>{keys.map((k, i) => { const m = d.metrics[k]!; return (
                  <tr key={k} className={cn("border-t", k === "strategy" && "bg-primary/5 font-medium")}>
                    <td className="py-1.5 pr-2"><span className="mr-1.5 inline-block h-0.5 w-3 rounded align-middle" style={{ background: `var(${COLORS[i % COLORS.length]})` }} />{d.names[k]}</td>
                    <td className={m.total_return >= 0 ? "text-up" : "text-down"}>{frac(m.total_return)}</td><td className={m.cagr >= 0 ? "text-up" : "text-down"}>{frac(m.cagr)}</td>
                    <td className="text-down">{frac(m.max_drawdown)}</td><td>{frac(m.volatility, 1, false)}</td><td>{num(m.sharpe, 2)}</td>
                  </tr>); })}</tbody>
              </table>
            </div>
          </Section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
            <Section title="자산 곡선" subtitle={`초기자금 ${man(d.params.capital)} · 지수는 배당이 빠진 가격 지수`}>
              <LineChart height={330} format={man} lines={keys.map((k, i) => ({ name: d.names[k], color: COLORS[i % COLORS.length], points: d.equity[k] ?? [], width: k === "strategy" ? 2 as const : 1 as const }))} />
            </Section>
            <div className="space-y-4">
              <Section title="비용 · 세금" subtitle={`${d.months}개월 누적 · 초기자금 ${man(d.params.capital)} 기준`}>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {([["수수료", d.costs.commission], ["슬리피지", d.costs.slippage], ["매도 거래세", d.costs.sell_tax], ["양도소득세 (낸 것)", d.costs.gains_tax]] as const).map(([k, v]) => (
                    <div key={k}><dt className="text-[11px] text-muted-foreground">{k}</dt><dd className="text-sm font-semibold tabular">{won(v)}</dd></div>))}
                  <div><dt className="text-[11px] text-muted-foreground">합계</dt><dd className="text-sm font-semibold tabular text-down">{won(d.costs.total)}</dd></div>
                  <div><dt className="text-[11px] text-muted-foreground">연 수익률 감소</dt><dd className="text-sm font-semibold tabular text-down">{isNum(d.costs.drag) ? `${frac(-d.costs.drag)}p` : "—"}</dd></div>
                  <div><dt className="text-[11px] text-muted-foreground">월평균 교체율</dt><dd className="text-sm font-semibold tabular">{frac(d.avg_turnover, 0, false)}</dd></div>
                  {d.costs.tax_accrued > 0 && <div><dt className="text-[11px] text-muted-foreground">아직 안 낸 올해 세금</dt><dd className="text-sm font-semibold tabular">{won(d.costs.tax_accrued)}</dd></div>}
                </dl>
              </Section>
              <Section title="연도별 수익률">
                <div className="overflow-x-auto">
                  <table className="w-full text-xs tabular">
                    <thead><tr className="text-left text-muted-foreground"><th className="font-normal">연도</th><th className="font-normal">교체 (후)</th><th className="font-normal">그대로 보유</th><th className="font-normal">S&P 500</th><th className="font-normal">코스피</th></tr></thead>
                    <tbody>{d.yearly.map((y) => (
                      <tr key={y.year} className="border-t"><td className="py-1.5">{y.year}</td>
                        {(["strategy", "hold_first", "sp500", "kospi"] as const).map((k) => <td key={k} className={cn(k === "strategy" && "font-medium", isNum(y[k]) ? (y[k]! >= 0 ? "text-up" : "text-down") : "text-muted-foreground")}>{frac(y[k])}</td>)}
                      </tr>))}</tbody>
                  </table>
                </div>
              </Section>
            </div>
          </div>

          <Section title={`이번 달 보유 종목 (${d.current.trade_date} 교체)`} subtitle="가장 최근 월말 순위의 상위 종목 · 과매수·과매도 제외 규칙 적용 후">
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {d.current.picks.map((p, i) => (
                <li key={p.symbol}><Link href={`/stock/${encodeURIComponent(p.symbol)}`} className="flex items-center justify-between gap-2 rounded-md border px-3 py-2 transition-colors hover:bg-accent/50">
                  <span className="min-w-0"><span className="mr-1.5 text-[11px] text-muted-foreground tabular">{i + 1}</span><span className="text-[13px] font-medium">{p.name}</span>
                    <span className="block truncate pl-4 font-mono text-[10.5px] text-muted-foreground">{p.symbol}</span></span>
                  <Pill tone="primary">{num(p.score, 1)}</Pill>
                </Link></li>
              ))}
            </ul>
            <details className="mt-4 text-xs">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">지난 리밸런싱 {d.rebalances.length}회 보기</summary>
              <div className="mt-2 max-h-96 overflow-auto">
                <table className="w-full text-xs tabular">
                  <thead className="sticky top-0 bg-card"><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">교체일</th><th className="font-normal">다음 교체까지</th><th className="font-normal">순위 대상</th><th className="font-normal">보유 종목</th></tr></thead>
                  <tbody>{d.rebalances.map((rb) => (
                    <tr key={rb.trade_date} className="border-t align-top"><td className="whitespace-nowrap py-1.5 pr-3">{rb.trade_date}{rb.open ? " (진행 중)" : ""}</td>
                      <td className={cn("pr-3", isNum(rb.return) ? (rb.return >= 0 ? "text-up" : "text-down") : "")}>{frac(rb.return)}</td>
                      <td className="whitespace-nowrap pr-3 text-muted-foreground">{rb.scored - rb.excluded} / {rb.scored}</td>
                      <td className="text-muted-foreground">{rb.picks.map((p) => p.name).join(" · ")}</td></tr>))}</tbody>
                </table>
              </div>
            </details>
          </Section>

          <Section title="이 결과를 읽을 때 주의할 점">
            <ul className="space-y-1.5 text-xs leading-relaxed text-muted-foreground">{d.notes.map((n) => <li key={n} className="flex gap-2"><span aria-hidden>·</span><span>{n}</span></li>)}</ul>
            <p className="mt-3 text-[11px] text-muted-foreground">{d.disclaimer}</p>
          </Section>
        </>
      )}
    </div>
  );
}
