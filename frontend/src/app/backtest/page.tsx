"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Play } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { type ReactNode, Suspense, useEffect, useRef, useState } from "react";

import { Builder } from "@/components/backtest/builder";
import { Rotation } from "@/components/backtest/rotation";
import { BTN, BenchTable, COLORS, INPUT, type Metrics, MetricsGrid, RegimePanel, type Regimes, type Trade, TradesTable, usd } from "@/components/backtest/shared";
import { LineChart } from "@/components/charts/line-chart";
import { PageHeader, Pill, Section, Segmented, SkeletonBlock, Unavailable } from "@/components/common";
import { api, post } from "@/lib/api";
import { frac, num } from "@/lib/format";

interface Strategy { id: string; name: string; description: string; defaults: Record<string, number>; grid: Record<string, number[]>; needs_regime: boolean }
interface BtResult {
  symbol: string; strategy: { id: string; name: string; description: string; params: Record<string, number> }; metrics: Metrics; trades: Trade[];
  equity: { date: string; value: number }[]; benchmarks: Record<string, { metrics: Metrics; equity: { date: string; value: number }[] }>;
  config: { start: string; end: string; commission_bps: number; slippage_bps: number }; disclaimer: string; regimes?: Regimes | null;
}
interface Period { range: [string, string]; metrics: Metrics; role: string; trades?: Trade[]; equity?: { date: string; value: number }[]; buy_and_hold?: Metrics | null }
interface WfSplit { symbol: string; mode: "split"; strategy: { name: string }; chosen_params: Record<string, number>; grid_size: number; train_ranking: { params: Record<string, number>; sharpe: number | null; cagr: number; trades: number }[]; periods: Record<string, Period>; degradation: { train_sharpe: number | null; test_sharpe: number | null; note: string } }
interface WfRolling { symbol: string; mode: "rolling"; strategy: { name: string }; folds: { train: string[]; test: string[]; params: Record<string, number>; train_sharpe: number | null; test_metrics: Metrics }[]; oos_equity: { date: string; value: number }[]; oos_metrics: Metrics; config: { train_years: number; test_months: number } }

const PARAM_KO: Record<string, string> = {
  fast: "단기 이평", slow: "장기 이평", rsi_min: "최소 RSI", cross_window: "크로스 유효 기간(일)", stop_below_slow: "장기 이평 하회 손절폭",
  entry_window: "진입 고점 기간(일)", exit_window: "청산 저점 기간(일)", vol_mult: "거래량 배수", rsi_entry: "진입 RSI", rsi_exit: "청산 RSI",
};
const PERIOD_KO: Record<string, string> = { train: "학습", validation: "검증", test: "표본 외 (OOS)", live: "실전 모니터링" };
const paramText = (p: Record<string, number>) => Object.entries(p).map(([k, v]) => `${PARAM_KO[k] ?? k}=${v}`).join(", ");

type Tab = "builder" | "fixed" | "rotation" | "wf";
const TABS: { value: Tab; label: string }[] = [
  { value: "builder", label: "전략 빌더" }, { value: "fixed", label: "기본 전략 A–D" }, { value: "rotation", label: "순위표 교체" }, { value: "wf", label: "워크포워드 검증" },
];
const TAB_HELP: Record<Tab, string> = {
  builder: "조건을 직접 조합해 내 규칙을 만들고, 이름을 붙여 저장한 뒤 바로 백테스트합니다.",
  fixed: "미리 정의된 전략 A–D를 파라미터만 바꿔 돌려봅니다.",
  rotation: "한 종목이 아니라 순위표 상위 종목을 매달 갈아타는 전략 — 수수료 · 세금 포함.",
  wf: "파라미터를 학습 구간에서 고르고, 한 번도 보지 않은 구간에서 검증합니다.",
};
interface Common { ticker: string; start: string; end: string; cost: { commission_bps: string; slippage_bps: string } }
interface Picked { sid: string; setSid: (s: string) => void; strategies?: Strategy[] }

function Fixed({ ticker, start, end, cost, sid, setSid, strategies }: Common & Picked) {
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});
  const strat = strategies?.find((s) => s.id === sid);
  const params: Record<string, string> = { ...Object.fromEntries(Object.entries(strat?.defaults ?? {}).map(([k, v]) => [k, String(v)])), ...(edits[sid] ?? {}) };
  const setParams = (p: Record<string, string>) => setEdits((e) => ({ ...e, [sid]: p }));
  const bt = useMutation({
    mutationFn: () => post<BtResult>("/api/backtest/run", { ticker, strategy: sid, start, end, params: Object.fromEntries(Object.entries(params).map(([k, v]) => [k, Number(v)])), commission_bps: Number(cost.commission_bps), slippage_bps: Number(cost.slippage_bps) }),
  });
  // First run waits for the strategy list so the request always carries the default parameters.
  const booted = useRef(false);
  useEffect(() => { if (strat && !booted.current) { booted.current = true; bt.mutate(); } }, [strat]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4">
      <Section title="기본 전략" subtitle="전략을 고르고 파라미터를 조정한 뒤 실행">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {strategies?.map((s) => (
            <button key={s.id} onClick={() => setSid(s.id)} className={cn("rounded-lg border p-3 text-left text-xs transition-colors", sid === s.id ? "border-primary bg-primary/5" : "hover:bg-accent/50")}>
              <div className="text-sm font-semibold">{s.id}. {s.name}</div>
              <p className="mt-1 text-muted-foreground">{s.description}</p>
            </button>
          ))}
        </div>
        {strat && (
          <div className="mt-3 flex flex-wrap items-end gap-3">
            {Object.keys(strat.defaults).map((k) => (
              <label key={k} className="text-xs">{PARAM_KO[k] ?? k}<input value={params[k] ?? ""} onChange={(e) => setParams({ ...params, [k]: e.target.value })} className={cn(INPUT, "mt-1 block w-24 tabular")} /></label>
            ))}
            <button onClick={() => bt.mutate()} disabled={bt.isPending} className={BTN}><Play className="size-4" />{ticker} 백테스트</button>
          </div>
        )}
      </Section>
      {bt.isPending ? <Section title="결과"><SkeletonBlock rows={8} /></Section> : bt.isError ? <Unavailable message={(bt.error as Error).message} onRetry={() => bt.mutate()} label="백테스트" /> : bt.data && (
        <>
          <Section title={`${bt.data.symbol} · ${bt.data.strategy.name}`} subtitle={`${bt.data.metrics.start} → ${bt.data.metrics.end} · 파라미터 ${paramText(bt.data.strategy.params)} · 비용 편도 ${bt.data.config.commission_bps}+${bt.data.config.slippage_bps}bp`}>
            <MetricsGrid m={bt.data.metrics} />
          </Section>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <Section title="자산 곡선 vs 벤치마크" subtitle="$10,000로 시작 · 벤치마크는 같은 기간 단순 보유 (지수는 가격 수익률)">
              <LineChart height={320} format={usd}
                lines={[{ name: "전략", color: COLORS[0], points: bt.data.equity, width: 2 },
                  ...Object.entries(bt.data.benchmarks).map(([k, v], i) => ({ name: k, color: COLORS[i + 1], points: v.equity, width: 1 as const }))]} />
            </Section>
            <Section title="전략 vs 벤치마크">
              <BenchTable highlight="전략" rows={[["전략", bt.data.metrics] as const, ...Object.entries(bt.data.benchmarks).map(([k, v]) => [k, v.metrics] as const)]} />
              <p className="mt-3 text-[11px] text-muted-foreground">{bt.data.disclaimer}</p>
            </Section>
          </div>
          <RegimePanel r={bt.data.regimes} />
          <Section title={`거래 내역 (${bt.data.trades.length}건)`}><TradesTable trades={bt.data.trades} /></Section>
        </>
      )}
    </div>
  );
}

function WalkForward({ ticker, cost, sid, setSid, strategies }: Pick<Common, "ticker" | "cost"> & Picked) {
  const [mode, setMode] = useState<"split" | "rolling">("split");
  const [periods, setPeriods] = useState({ train: ["2021-01-01", "2023-12-31"], validation: ["2024-01-01", "2024-12-31"], test: ["2025-01-01", "2025-12-31"] });
  const wf = useMutation({
    mutationFn: () => post<WfSplit | WfRolling>("/api/backtest/walk-forward", mode === "split"
      ? { ticker, strategy: sid, mode, ...periods, commission_bps: Number(cost.commission_bps), slippage_bps: Number(cost.slippage_bps) }
      : { ticker, strategy: sid, mode, train_years: 3, test_months: 6 }),
  });
  const run = <button onClick={() => wf.mutate()} disabled={wf.isPending} className={BTN}><Play className="size-4" />{ticker} 실행</button>;

  return (
    <div className="space-y-4">
      <Section title="워크포워드 검증" subtitle="파라미터는 학습 구간에서 고르고, 검증 구간에서 확인한 뒤, 한 번도 보지 않은 표본 외(OOS) 구간에서 단 한 번 평가합니다."
        right={<Segmented size="xs" value={mode} onChange={setMode} options={[{ value: "split", label: "학습/검증/OOS" }, { value: "rolling", label: "롤링" }]} />}>
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">전략</span>
          {strategies && <Segmented size="xs" ariaLabel="검증할 전략" value={sid} onChange={setSid} options={strategies.map((s) => ({ value: s.id, label: `${s.id}. ${s.name}` }))} />}
        </div>
        {mode === "split" ? (
          <div className="flex flex-wrap items-end gap-3">
            {(["train", "validation", "test"] as const).map((k) => (
              <div key={k} className="text-xs">
                <div className="mb-1">{PERIOD_KO[k]}</div>
                <input type="date" value={periods[k][0]} onChange={(e) => setPeriods({ ...periods, [k]: [e.target.value, periods[k][1]] })} className={INPUT} />
                <span className="mx-1">→</span>
                <input type="date" value={periods[k][1]} onChange={(e) => setPeriods({ ...periods, [k]: [periods[k][0], e.target.value] })} className={INPUT} />
              </div>
            ))}
            {run}
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-xs"><span>3년 학습 · 6개월 테스트 구간을 앞으로 굴려가며 반복하고, 테스트 구간들을 이어 하나의 표본 외 기록으로 만듭니다.</span>{run}</div>
        )}
        <p className="mt-2 text-[11px] text-muted-foreground">실전 모니터링 = 표본 외 구간 이후부터 오늘까지, 같은 파라미터를 고정한 채 추적한 성과.</p>
      </Section>
      {wf.isPending ? <SkeletonBlock rows={6} /> : wf.isError ? <Unavailable message={(wf.error as Error).message} onRetry={() => wf.mutate()} label="워크포워드" /> : wf.data && (wf.data.mode === "split" ? (() => {
        const d = wf.data as WfSplit;
        return (
          <>
            <Section title={`${d.symbol} · ${d.strategy.name}`} subtitle={`파라미터 조합 ${d.grid_size}개 중 선택: ${paramText(d.chosen_params)}`}>
              <div className="overflow-x-auto">
                <table className="w-full text-xs tabular">
                  <thead><tr className="text-left text-muted-foreground"><th className="font-normal">구간</th><th className="font-normal">기간</th><th className="font-normal">역할</th><th className="font-normal">CAGR</th><th className="font-normal">샤프</th><th className="font-normal">MDD</th><th className="font-normal">거래</th><th className="font-normal">승률</th><th className="font-normal">단순 보유</th></tr></thead>
                  <tbody>{Object.entries(d.periods).map(([k, p]) => (
                    <tr key={k} className={cn("border-t", k === "test" && "bg-primary/5")}>
                      <td className="py-1.5 font-semibold">{PERIOD_KO[k] ?? k}</td><td>{p.range[0]} → {p.range[1]}</td><td className="text-muted-foreground">{p.role}</td>
                      <td className={p.metrics.cagr >= 0 ? "text-up" : "text-down"}>{frac(p.metrics.cagr)}</td><td>{num(p.metrics.sharpe, 2)}</td><td className="text-down">{frac(p.metrics.max_drawdown)}</td>
                      <td>{p.metrics.trades ?? "—"}</td><td>{frac(p.metrics.win_rate, 0, false)}</td><td>{p.buy_and_hold ? frac(p.buy_and_hold.cagr) : "—"}</td>
                    </tr>))}</tbody>
                </table>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <Pill>학습 샤프 {num(d.degradation.train_sharpe, 2)}</Pill><Pill tone="primary">표본 외 샤프 {num(d.degradation.test_sharpe, 2)}</Pill>
                <span className="text-muted-foreground">{d.degradation.note}</span>
              </div>
            </Section>
            {d.periods.test?.equity && (
              <Section title="표본 외 · 실전 모니터링 자산 곡선">
                <LineChart height={260} format={usd} lines={[
                  { name: "표본 외", color: "--s1", points: d.periods.test.equity }, ...(d.periods.live?.equity ? [{ name: "실전", color: "--s2", points: d.periods.live.equity }] : [])]} />
              </Section>
            )}
            <Section title="학습 구간 상위 파라미터 조합" subtitle="표본 내 순위 - 투명성을 위해 공개할 뿐, 성과의 근거가 아닙니다">
              <ul className="grid gap-1 text-xs tabular md:grid-cols-2">{d.train_ranking.map((r, i) => (
                <li key={i} className="flex justify-between rounded border px-2 py-1"><span>{paramText(r.params)}</span><span>샤프 {num(r.sharpe, 2)} · 거래 {r.trades}회</span></li>))}</ul>
            </Section>
          </>
        );
      })() : (() => {
        const d = wf.data as WfRolling;
        return (
          <>
            <Section title={`${d.symbol} · 롤링 워크포워드`} subtitle={`${d.folds.length}개 구간 · 학습 ${d.config.train_years}년 / 테스트 ${d.config.test_months}개월`}><MetricsGrid m={d.oos_metrics} /></Section>
            <Section title="이어 붙인 표본 외 자산 곡선"><LineChart height={260} area lines={[{ name: "표본 외", color: "--s1", points: d.oos_equity }]} format={usd} /></Section>
            <Section title="구간별 결과">
              <div className="overflow-x-auto">
                <table className="w-full text-xs tabular"><thead><tr className="text-left text-muted-foreground"><th className="font-normal">학습</th><th className="font-normal">테스트</th><th className="font-normal">선택된 파라미터</th><th className="font-normal">학습 샤프</th><th className="font-normal">테스트 수익률</th><th className="font-normal">테스트 샤프</th></tr></thead>
                  <tbody>{d.folds.map((f) => (<tr key={f.test[0]} className="border-t"><td className="py-1">{f.train.join(" → ")}</td><td>{f.test.join(" → ")}</td><td>{paramText(f.params)}</td><td>{num(f.train_sharpe, 2)}</td>
                    <td className={f.test_metrics.total_return >= 0 ? "text-up" : "text-down"}>{frac(f.test_metrics.total_return)}</td><td>{num(f.test_metrics.sharpe, 2)}</td></tr>))}</tbody></table>
              </div>
            </Section>
          </>
        );
      })())}
    </div>
  );
}

function BacktestInner() {
  const sp = useSearchParams();
  const first = TABS.some((t) => t.value === sp.get("tab")) ? (sp.get("tab") as Tab) : "builder";
  const [tab, setTab] = useState<Tab>(first);
  const [seen, setSeen] = useState<Tab[]>([first]);
  const go = (t: Tab) => { setTab(t); setSeen((s) => (s.includes(t) ? s : [...s, t])); };
  const [ticker, setTicker] = useState(sp.get("ticker") ?? "NVDA");
  const [sid, setSid] = useState("A");
  const [start, setStart] = useState("2019-01-01");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [cost, setCost] = useState({ commission_bps: "5", slippage_bps: "5" });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => api<{ strategies: Strategy[]; execution: string }>("/api/backtest/strategies"), staleTime: Infinity });
  const common = { ticker, start, end, cost };
  const picked = { sid, setSid, strategies: strategies.data?.strategies };
  // Panes mount on first visit and stay mounted, so each tab keeps its inputs and results.
  const pane = (t: Tab, node: () => ReactNode) => <div className={tab === t ? "" : "hidden"}>{seen.includes(t) && node()}</div>;

  return (
    <div className="space-y-5">
      <PageHeader title="백테스트 · 전략 연구" description={strategies.data?.execution} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Segmented ariaLabel="백테스트 종류" value={tab} onChange={go} options={TABS} />
        <p className="text-xs text-muted-foreground">{TAB_HELP[tab]}</p>
      </div>
      {tab !== "rotation" && (
        <div className="flex flex-wrap items-end gap-3 rounded-xl border bg-card px-4 py-3 shadow-card">
          <label className="text-xs">종목<input value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} className={cn(INPUT, "mt-1 block w-28 font-mono")} /></label>
          {tab !== "wf" && <>
            <label className="text-xs">시작일<input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={cn(INPUT, "mt-1 block")} /></label>
            <label className="text-xs">종료일<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={cn(INPUT, "mt-1 block")} /></label>
          </>}
          <label className="text-xs">수수료 (bp)<input value={cost.commission_bps} onChange={(e) => setCost({ ...cost, commission_bps: e.target.value })} className={cn(INPUT, "mt-1 block w-20")} /></label>
          <label className="text-xs">슬리피지 (bp)<input value={cost.slippage_bps} onChange={(e) => setCost({ ...cost, slippage_bps: e.target.value })} className={cn(INPUT, "mt-1 block w-20")} /></label>
          <p className="pb-1.5 text-[11px] text-muted-foreground">모든 탭이 이 설정을 함께 씁니다 · 비용은 편도 기준</p>
        </div>
      )}
      {pane("builder", () => <Builder {...common} />)}
      {pane("fixed", () => <Fixed {...common} {...picked} />)}
      {pane("rotation", () => <Rotation />)}
      {pane("wf", () => <WalkForward ticker={ticker} cost={cost} {...picked} />)}
    </div>
  );
}

export default function BacktestPage() {
  return <Suspense fallback={<SkeletonBlock rows={6} />}><BacktestInner /></Suspense>;
}
