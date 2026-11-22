"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Check, OctagonX, Play, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { LineChart } from "@/components/charts/line-chart";
import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock } from "@/components/common";
import { Switch } from "@/components/ui/switch";
import { api, del, patch, post } from "@/lib/api";
import { isNum, pct, signClass } from "@/lib/format";

interface Kind { id: string; name: string; family: string; timeframe: "daily" | "intraday"; intervals: string[]; day_trade: boolean; description: string;
  defaults: Record<string, number>; labels: Record<string, string>; source: { title: string; url: string } | null }
interface CostStats { trades: number; win_rate: number; avg_ret_pct: number; avg_win_pct: number | null; avg_loss_pct: number | null; profit_factor: number | null;
  ann_ret_pct: number | null; ann_ret_invested_pct: number | null; first_half_avg_pct: number | null; second_half_avg_pct: number | null; pct_symbols_positive: number | null }
interface StudyRow { kind: string; name: string; family: string; timeframe: string; interval: string; description: string; source: { title: string; url: string } | null;
  data: string; eligible: boolean; verdict: string; score?: number;
  main: { gross: CostStats; event: CostStats; standard: CostStats; symbols: number; years: number; exposure_pct: number; median_dd_pct: number;
    buy_hold_ann_pct: number | null; trades_per_symbol_year: number; open_at_end: number; best_symbols: string[] };
  etf_10y: { event: CostStats; buy_hold_ann_pct: number | null; symbols: number } | null }
interface Study { status: { state: string; message: string | null; progress: number }; study: { generated_at: string; picks: string[]; results: StudyRow[]; method: string } | null }
interface Strategy { id: number; name: string; kind: string; kind_name: string; interval: string; params: Record<string, number>; symbols: string[];
  max_position_usd: number; flatten_at_close: boolean; enabled: boolean; state: Record<string, { last_bar?: string; note?: string; checked_at?: string }>;
  closed: number; win_rate: number | null; realized_pl: number }
interface Position { id: number; symbol: string; strategy: string; qty: number; avg_price: number; price: number | null; stop_price: number | null;
  value: number | null; pl: number | null; pl_pct: number | null; opened_at: string }
interface Trade { id: number; ts: string; symbol: string; side: "buy" | "sell"; qty: number; price: number; fee: number; realized_pl: number | null; reason: string; strategy: string }
interface Event { ts: string; kind: string; symbol: string | null; message: string; strategy: string | null }
interface Paper {
  account: { cash: number; equity: number; start_cash: number; total_pl: number; total_ret_pct: number | null; day_pl: number | null; fee_bps: number;
    slippage_bps: number; daily_loss_limit_pct: number; paused: boolean; halted_today: boolean; fees_paid: number };
  market: { state: string; next_open: string | null; session_close: string | null };
  positions: Position[]; strategies: Strategy[]; trades: Trade[]; events: Event[]; equity_curve: { ts: string; equity: number }[];
  kinds: Kind[]; intervals: string[]; manual: { closed: number; win_rate: number | null; realized_pl: number };
}
interface BtRow { symbol: string; error?: string; trades?: number; win_rate?: number | null; total_pl?: number; fees?: number; pl_no_cost?: number;
  buy_hold_pct?: number; trades_per_day?: number | null; max_dd?: number }
interface Backtest { interval: string; rows: BtRow[]; total: { trades: number; win_rate: number | null; total_pl: number; fees: number; pl_no_cost: number; days: number;
  notional: number; from: string; to: string } | null; note: string }

const usd = (v: number | null | undefined, signed = false) => (isNum(v) ? `${signed && v > 0 ? "+" : ""}${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—");
const STAGES = ["앱 안 모의매매", "LS 모의투자 연결", "소액 실전 · 주문마다 승인", "자동 실행 · 한도 · 긴급 정지"];
const MARKET_KO: Record<string, string> = { open: "미국 정규장 열림", pre: "프리마켓", post: "애프터마켓", closed: "장 마감" };
const kst = (iso: string) => new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

interface Draft { id?: number; name: string; kind: string; interval: string; symbols: string; max: string; flatten: boolean; params: Record<string, string> }

export default function TradingPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["paper"], queryFn: () => api<Paper>("/api/paper"), refetchInterval: 30_000 });
  const set = (d: Paper) => qc.setQueryData(["paper"], d);
  const onErr = (e: Error) => toast.error(e.message);
  const acct = useMutation({ mutationFn: (b: Record<string, unknown>) => patch<Paper>("/api/paper/account", b), onSuccess: set, onError: onErr });
  const run = useMutation({ mutationFn: () => post<Paper & { result: { state: string; orders?: number } }>("/api/paper/run"),
    onSuccess: (d) => { set(d); toast.message(d.result.state === "ran" ? `실행 완료 · 주문 ${d.result.orders ?? 0}건` : d.result.state === "market closed" ? "장이 닫혀 있어 실행하지 않았습니다" : "긴급 정지 중입니다"); }, onError: onErr });
  const reset = useMutation({ mutationFn: (start: number) => post<Paper>("/api/paper/reset", { start_cash: start }), onSuccess: (d) => { set(d); toast.success("계좌를 초기화했습니다"); }, onError: onErr });

  return (
    <div className="space-y-5">
      <PageHeader title="자동매매 (모의)" description="규칙 기반 전략을 실시간 시세로 가상 체결합니다. 증권사에 주문을 보내지 않습니다." />
      <ol className="flex flex-wrap gap-2 text-xs">
        {STAGES.map((s, i) => (
          <li key={s} className={cn("flex items-center gap-1.5 rounded-md border px-2 py-1", i === 0 ? "border-primary bg-primary/10 font-medium" : "text-muted-foreground")}>
            <span className={cn("grid size-4 place-items-center rounded-full text-[10px]", i === 0 ? "bg-primary text-primary-foreground" : "bg-muted")}>{i + 1}</span>{s}{i === 0 && " (지금)"}
          </li>))}
      </ol>
      <QueryState q={q} label="모의매매" skeleton={<SkeletonBlock rows={10} />}>
        {(d) => (
          <>
            <AccountCard d={d} onPause={(v) => acct.mutate({ paused: v })} onRun={() => run.mutate()} running={run.isPending}
              onSettings={(b) => acct.mutate(b)} onReset={(c) => reset.mutate(c)} />
            {d.equity_curve.length > 1 && (
              <Section title="평가금액 추이"><LineChart area lines={[{ name: "평가금액", color: "--s1", points: d.equity_curve.map((p) => ({ date: Math.floor(new Date(p.ts).getTime() / 1000), value: p.equity })) }]} height={200} format={(v) => `$${v.toFixed(0)}`} /></Section>
            )}
            <Strategies d={d} onChange={set} />
            <Positions d={d} onChange={set} />
            <ManualOrder onChange={set} />
            <Activity d={d} />
          </>
        )}
      </QueryState>
    </div>
  );
}

function AccountCard({ d, onPause, onRun, running, onSettings, onReset }: { d: Paper; onPause: (v: boolean) => void; onRun: () => void; running: boolean;
  onSettings: (b: Record<string, number>) => void; onReset: (c: number) => void }) {
  const a = d.account;
  const [fee, setFee] = useState(String(a.fee_bps / 100));
  const [slip, setSlip] = useState(String(a.slippage_bps));
  const [limit, setLimit] = useState(String(a.daily_loss_limit_pct));
  const input = "mt-1 h-8 w-20 rounded-md border bg-background px-2 text-sm tabular";
  return (
    <Section title="모의 계좌 (USD)" right={<span className="flex items-center gap-1.5">{MARKET_KO[d.market.state] ?? d.market.state}{d.market.next_open && ` · 다음 개장 ${kst(d.market.next_open)}`}</span>}>
      {a.paused && <p className="mb-3 rounded-md border border-down/40 bg-down/10 p-2 text-sm text-down"><b>긴급 정지 중</b> - 자동 주문(매수 · 매도 · 손절)이 모두 멈춰 있습니다.</p>}
      {a.halted_today && !a.paused && <p className="mb-3 rounded-md border border-warn/40 bg-warn/10 p-2 text-sm">오늘 일일 손실 한도(−{a.daily_loss_limit_pct}%)에 닿아 신규 매수를 멈췄습니다. 손절 · 청산은 계속됩니다.</p>}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        {[["평가금액", usd(a.equity)], ["현금", usd(a.cash)],
          ["총 손익", <span key="t" className={signClass(a.total_pl)}>{usd(a.total_pl, true)} ({pct(a.total_ret_pct)})</span>],
          ["오늘 손익", <span key="d" className={signClass(a.day_pl)}>{usd(a.day_pl, true)}</span>],
          ["낸 수수료", usd(a.fees_paid)]].map(([k, v]) => (
          <div key={String(k)} className="rounded-xl border bg-card p-3.5 shadow-card"><div className="text-[11px] text-muted-foreground">{k}</div><div className="mt-1 text-lg font-semibold tabular">{v}</div></div>))}
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <button onClick={() => onPause(!a.paused)} className={cn("inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm font-medium", a.paused ? "border" : "bg-down text-white")}>
          {a.paused ? <><Play className="size-4" />자동 주문 재개</> : <><OctagonX className="size-4" />긴급 정지</>}</button>
        <button onClick={onRun} disabled={running} className="h-8 rounded-md border px-3 text-sm hover:bg-accent disabled:opacity-60">{running ? "실행 중…" : "지금 한 번 실행"}</button>
        <span className="mx-1 h-6 w-px bg-border" />
        <label className="text-xs">수수료 (%/회)<input value={fee} onChange={(e) => setFee(e.target.value)} inputMode="decimal" className={cn(input, "block")} /></label>
        <label className="text-xs">슬리피지 (bp)<input value={slip} onChange={(e) => setSlip(e.target.value)} inputMode="decimal" className={cn(input, "block")} /></label>
        <label className="text-xs">일일 손실 한도 (%)<input value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" className={cn(input, "block")} /></label>
        <button onClick={() => onSettings({ fee_bps: Number(fee) * 100, slippage_bps: Number(slip), daily_loss_limit_pct: Number(limit) })}
          className="h-8 rounded-md border px-3 text-sm hover:bg-accent">설정 저장</button>
        <button onClick={() => { const v = window.prompt("시작 자금($)으로 계좌를 초기화합니다. 거래 기록과 포지션이 모두 지워지고 전략은 꺼집니다.", String(a.start_cash)); if (v && Number(v) >= 100) onReset(Number(v)); }}
          className="h-8 rounded-md px-3 text-sm text-muted-foreground hover:bg-accent">계좌 초기화</button>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">수수료 기본값 0.25%/회는 보수적인 가정입니다. 실제 증권사 해외주식 수수료(이벤트 요율 포함)로 바꿔 주세요. 전략은 1분마다, 미국 정규장(한국 시간 밤)에만 돌고, 완성된 봉으로만 판단합니다.</p>
    </Section>
  );
}

function emptyDraft(k: Kind): Draft {
  const daily = k.timeframe === "daily";
  return { name: k.name, kind: k.id, interval: daily ? "1d" : k.intervals.includes("15m") ? "15m" : k.intervals[0],
    symbols: daily ? "SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META" : "NVDA, TSLA", max: "1000", flatten: !daily,
    params: Object.fromEntries(Object.entries(k.defaults).map(([a, b]) => [a, String(b)])) };
}

function Strategies({ d, onChange }: { d: Paper; onChange: (d: Paper) => void }) {
  const kinds = d.kinds;
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(kinds.find((k) => k.id === "bb_reversion") ?? kinds[0]));
  const [bt, setBt] = useState<{ title: string; res: Backtest } | null>(null);
  const kind = kinds.find((k) => k.id === draft.kind) ?? kinds[0];
  const body = (x: Draft) => ({ name: x.name, kind: x.kind, interval: x.interval, symbols: x.symbols.split(/[,\s]+/).filter(Boolean),
    max_position_usd: Number(x.max), flatten_at_close: x.flatten, params: Object.fromEntries(Object.entries(x.params).map(([a, b]) => [a, Number(b)])) });
  const save = useMutation({
    mutationFn: (x: Draft) => (x.id ? patch<Paper>(`/api/paper/strategies/${x.id}`, body(x)) : post<Paper>("/api/paper/strategies", body(x))),  // kind is fixed once saved (ignored on PATCH)
    onSuccess: (r) => { onChange(r); toast.success(draft.id ? "전략을 수정했습니다" : "전략을 추가했습니다 (꺼진 상태)"); setDraft(emptyDraft(kind)); },
    onError: (e: Error) => toast.error(e.message),
  });
  const toggle = useMutation({ mutationFn: ({ id, on }: { id: number; on: boolean }) => patch<Paper>(`/api/paper/strategies/${id}`, { enabled: on }), onSuccess: onChange, onError: (e: Error) => toast.error(e.message) });
  const remove = useMutation({ mutationFn: (id: number) => del<Paper>(`/api/paper/strategies/${id}`), onSuccess: onChange, onError: (e: Error) => toast.error(e.message) });
  const backtest = useMutation({ mutationFn: (x: { title: string; b: ReturnType<typeof body> }) => post<Backtest>("/api/paper/backtest", x.b).then((res) => ({ title: x.title, res })),
    onSuccess: setBt, onError: (e: Error) => toast.error(e.message) });
  const input = "mt-1 h-8 rounded-md border bg-background px-2 text-sm";

  return (
    <>
    <StudyPanel kinds={kinds} onPick={(k) => { setDraft(emptyDraft(k)); document.getElementById("strategy-form")?.scrollIntoView({ behavior: "smooth", block: "center" }); }} />
    <Section title="내 전략" subtitle="켜 두면 장중 1분마다 확인합니다 (일봉 전략은 하루 한 번, 전날 종가 기준). 먼저 백테스트로 과거 성적과 수수료 영향을 보세요.">
      {d.strategies.length === 0 && <p className="mb-3 text-sm text-muted-foreground">아직 전략이 없습니다. 아래에서 만들어 보세요.</p>}
      <ul className="mb-4 space-y-2">
        {d.strategies.map((s) => (
          <li key={s.id} className="rounded-lg border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Switch checked={s.enabled} onCheckedChange={(v) => toggle.mutate({ id: s.id, on: v })} aria-label={`${s.name} 켜기`} />
              <b>{s.name}</b><Pill>{s.kind_name}</Pill><Pill>{s.interval === "1d" ? "일봉" : `${s.interval}봉`}</Pill><span className="font-mono text-xs">{s.symbols.join(", ")}</span>
              <span className="text-xs text-muted-foreground">회당 ${s.max_position_usd.toLocaleString()}{s.flatten_at_close ? " · 장 마감 전 청산" : ""}</span>
              <span className="ml-auto flex gap-1">
                <button onClick={() => backtest.mutate({ title: s.name, b: body({ ...draft, ...toDraft(s) }) })} disabled={backtest.isPending} className="rounded-md border px-2 py-1 text-xs hover:bg-accent">백테스트</button>
                <button onClick={() => setDraft(toDraft(s))} className="rounded-md border px-2 py-1 text-xs hover:bg-accent">수정</button>
                <button onClick={() => window.confirm(`'${s.name}'을(를) 삭제할까요? 이 전략의 보유분은 현재가로 청산됩니다.`) && remove.mutate(s.id)} className="rounded-md p-1 text-muted-foreground hover:bg-accent" aria-label="삭제"><Trash2 className="size-4" /></button>
              </span>
            </div>
            <div className="mt-1 text-xs text-muted-foreground">청산 {s.closed}건 · 승률 {isNum(s.win_rate) ? `${s.win_rate.toFixed(0)}%` : "—"} · 실현 손익 <span className={signClass(s.realized_pl)}>{usd(s.realized_pl, true)}</span></div>
            {Object.keys(s.state).length > 0 && (
              <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">{Object.entries(s.state).map(([sym, x]) => (
                <li key={sym}><span className="font-mono">{sym}</span> {x.note ?? ""}{x.checked_at ? ` · ${kst(x.checked_at)} 확인` : ""}</li>))}</ul>
            )}
          </li>))}
      </ul>

      <div id="strategy-form" className="rounded-lg border bg-muted/20 p-3">
        <div className="mb-2 text-sm font-medium">{draft.id ? "전략 수정" : "새 전략"}</div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs">종류<select value={draft.kind} disabled={!!draft.id} onChange={(e) => setDraft(emptyDraft(kinds.find((k) => k.id === e.target.value)!))} className={cn(input, "block")}>
            {["daily", "intraday"].map((tf) => (
              <optgroup key={tf} label={tf === "daily" ? "일봉 (스윙)" : "분봉 (당일 매매)"}>
                {kinds.filter((k) => k.timeframe === tf).map((k) => <option key={k.id} value={k.id}>{k.name} · {k.family}</option>)}
              </optgroup>))}</select></label>
          <label className="text-xs">이름<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={cn(input, "block w-40")} /></label>
          <label className="text-xs">종목 (쉼표, 미국만)<input value={draft.symbols} onChange={(e) => setDraft({ ...draft, symbols: e.target.value })} className={cn(input, "block w-48 font-mono")} /></label>
          <div className="text-xs">봉<div className="mt-1"><Segmented size="xs" ariaLabel="봉 간격" value={draft.interval} onChange={(v) => setDraft({ ...draft, interval: v })} options={kind.intervals.map((i) => ({ value: i, label: i === "1d" ? "일봉" : i }))} /></div></div>
          <label className="text-xs">1회 금액 ($)<input value={draft.max} onChange={(e) => setDraft({ ...draft, max: e.target.value })} inputMode="decimal" className={cn(input, "block w-24")} /></label>
          {Object.entries(kind.labels).map(([k, label]) => (
            <label key={k} className="text-xs">{label}<input value={draft.params[k] ?? ""} onChange={(e) => setDraft({ ...draft, params: { ...draft.params, [k]: e.target.value } })} inputMode="decimal" className={cn(input, "block w-20")} /></label>))}
          {kind.timeframe === "intraday" && !kind.day_trade && <label className="flex h-8 items-center gap-1.5 text-xs"><input type="checkbox" checked={draft.flatten} onChange={(e) => setDraft({ ...draft, flatten: e.target.checked })} />장 마감 전 청산</label>}
          {kind.day_trade && <span className="flex h-8 items-center text-xs text-muted-foreground">당일 매매 - 장 마감 전 항상 청산</span>}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{kind.description}{kind.source && <> · 출처 <a href={kind.source.url} target="_blank" rel="noreferrer" className="underline">{kind.source.title}</a></>}</p>
        <div className="mt-2 flex gap-2">
          <button onClick={() => backtest.mutate({ title: draft.name, b: body(draft) })} disabled={backtest.isPending} className="rounded-md border px-3 py-1.5 text-sm hover:bg-accent disabled:opacity-60">{backtest.isPending ? "백테스트 중…" : "백테스트"}</button>
          <button onClick={() => save.mutate(draft)} disabled={save.isPending} className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60"><Check className="size-4" />{draft.id ? "수정 저장" : "저장 (꺼진 상태로)"}</button>
          {draft.id && <button onClick={() => setDraft(emptyDraft(kind))} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">취소</button>}
        </div>
      </div>
      {bt && <BacktestView title={bt.title} r={bt.res} />}
    </Section>
    </>
  );
}

const signed = (v: number | null | undefined, d = 2) => (isNum(v) ? `${v > 0 ? "+" : ""}${v.toFixed(d)}%` : "—");

function StudyPanel({ kinds, onPick }: { kinds: Kind[]; onPick: (k: Kind) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["paper-study"], queryFn: () => api<Study>("/api/paper/study"),
    refetchInterval: (query) => (query.state.data?.status.state === "running" ? 4000 : false) });
  const rerun = useMutation({ mutationFn: () => post<Study>("/api/paper/study/run"), onSuccess: (d) => qc.setQueryData(["paper-study"], d) });
  const st = q.data?.study;
  const running = q.data?.status.state === "running";
  if (!q.data) return <Section title="전략 고르기"><SkeletonBlock rows={4} /></Section>;
  const rows = st?.results ?? [];
  const picks = (st?.picks ?? []).map((id) => rows.find((r) => r.kind === id)).filter((r): r is StudyRow => !!r);
  const bh = rows.find((r) => r.timeframe === "daily")?.main.buy_hold_ann_pct;
  const kindOf = (id: string) => kinds.find((k) => k.id === id);
  return (
    <Section title="전략 고르기 · 인터넷의 '고승률' 규칙 검증"
      subtitle={st ? `${rows.length}개 규칙을 원래 설정 그대로 백테스트 · ${new Date(st.generated_at).toLocaleDateString("ko-KR")} 계산 · 수수료 0.07%/회 + 슬리피지 0.05% 기준` : "아직 계산 전"}
      right={<button onClick={() => rerun.mutate()} disabled={running} className="rounded-md border px-2 py-1 text-xs hover:bg-accent disabled:opacity-60">{running ? `계산 중… ${Math.round((q.data.status.progress ?? 0) * 100)}%` : "다시 계산"}</button>}>
      {!st ? <p className="text-sm text-muted-foreground">결과가 없습니다 (Data unavailable). &lsquo;다시 계산&rsquo;을 누르면 1~2분 걸립니다.</p> : (
        <>
          <div className="mb-3 space-y-1 rounded-md border border-warn/40 bg-warn/5 p-2 text-xs">
            <p><b>승률과 수익률이 둘 다 높은 규칙은 없었습니다.</b> 승률형(평균회귀)은 자주 이기지만 한 번에 조금, 수익형(추세추종)은 40%대만 이기지만 한 번에 크게 법니다.</p>
            <p>분봉 당일 매매(ORB · VWAP · 변동성 돌파 · 분봉 추세)는 비용 전 본전 수준이라 수수료를 내면 전부 손실이었습니다.
              {isNum(bh) && <> 같은 기간 그냥 들고 있었다면 종목당 연 {bh.toFixed(1)}%로, 아래 어떤 규칙보다 높았습니다 (2022~2026 상승장).</>}</p>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {picks.map((r) => {
              const e = r.main.event, std = r.main.standard;
              const type = e.win_rate >= 55 ? "승률형" : "수익형";
              const k = kindOf(r.kind);
              return (
                <div key={r.kind} className="flex flex-col rounded-lg border p-3">
                  <div className="flex flex-wrap items-center gap-2"><b>{r.name}</b><Pill tone={type === "승률형" ? "primary" : "up"}>{type}</Pill><Pill>{r.family}</Pill><Pill>일봉 스윙</Pill></div>
                  <p className="mt-1 text-xs text-muted-foreground">{r.description}</p>
                  <div className="mt-2 grid grid-cols-3 gap-2 text-center text-xs">
                    {[["승률", `${e.win_rate.toFixed(0)}%`], ["거래당 평균", signed(e.avg_ret_pct)], ["연 수익률 (종목당)", signed(e.ann_ret_pct, 1)],
                      ["평균 이익 / 손실", `${signed(e.avg_win_pct, 1)} / ${signed(e.avg_loss_pct, 1)}`], ["투자 비중", `${r.main.exposure_pct.toFixed(0)}% 기간`],
                      ["최대 낙폭 (중앙값)", `${r.main.median_dd_pct.toFixed(1)}%`]].map(([a, b]) => (
                      <div key={a} className="rounded-md bg-muted/40 p-1.5"><div className="text-[10px] text-muted-foreground">{a}</div><div className="font-semibold tabular">{b}</div></div>))}
                  </div>
                  <ul className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
                    <li>· {r.data} · {e.trades.toLocaleString()}건 · 종목당 연 {r.main.trades_per_symbol_year.toFixed(1)}회 · 이익 난 종목 {e.pct_symbols_positive?.toFixed(0)}%</li>
                    <li>· 앞·뒤 기간 평균 {signed(e.first_half_avg_pct)} / {signed(e.second_half_avg_pct)} · 수수료 0.25%면 거래당 <span className={signClass(std.avg_ret_pct)}>{signed(std.avg_ret_pct)}</span></li>
                    {r.etf_10y?.event?.trades ? <li>· SPY·QQQ·IWM·DIA 10년: 승률 {r.etf_10y.event.win_rate.toFixed(0)}% · 거래당 {signed(r.etf_10y.event.avg_ret_pct)} ({r.etf_10y.event.trades}건)</li> : null}
                    {r.main.open_at_end > 0 && r.main.open_at_end / e.trades > 0.15 && <li className="text-warn">· 거래의 {(r.main.open_at_end / e.trades * 100).toFixed(0)}%가 기간 끝에 아직 보유 중 (평가손익으로 계산) - 거래 수가 적어 결과가 상승장에 많이 기댑니다</li>}
                    {r.source && <li>· 출처: <a href={r.source.url} target="_blank" rel="noreferrer" className="underline">{r.source.title}</a></li>}
                  </ul>
                  {k && <button onClick={() => onPick(k)} className="mt-auto self-start pt-2"><span className="inline-block rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground">이 전략으로 만들기</span></button>}
                </div>);
            })}
          </div>
          <details className="mt-3">
            <summary className="cursor-pointer text-sm">전체 {rows.length}개 결과 보기</summary>
            <div className="mt-2 overflow-x-auto"><table className="w-full min-w-[820px] text-xs tabular">
              <thead><tr className="border-b text-left text-muted-foreground">{["규칙", "유형", "승률", "거래당", "연 수익률", "비용 전 거래당", "0.25%일 때", "판정"].map((h) => <th key={h} className="py-1 pr-2 font-normal">{h}</th>)}</tr></thead>
              <tbody>{[...rows].sort((a, b) => (b.eligible ? 1 : 0) - (a.eligible ? 1 : 0) || (b.main.event?.avg_ret_pct ?? -9) - (a.main.event?.avg_ret_pct ?? -9)).map((r) => (
                <tr key={r.kind} className={cn("border-b last:border-0", st.picks.includes(r.kind) && "bg-primary/5")}>
                  <td className="py-1 pr-2">{r.name}<span className="block text-[10px] text-muted-foreground">{r.timeframe === "daily" ? "일봉" : `${r.interval}봉 당일`}</span></td>
                  <td className="pr-2">{r.family}</td>
                  <td className="pr-2">{isNum(r.main.event?.win_rate) ? `${r.main.event.win_rate.toFixed(0)}%` : "—"}</td>
                  <td className={cn("pr-2", signClass(r.main.event?.avg_ret_pct))}>{signed(r.main.event?.avg_ret_pct)}</td>
                  <td className={cn("pr-2", signClass(r.main.event?.ann_ret_pct))}>{signed(r.main.event?.ann_ret_pct, 1)}</td>
                  <td className={cn("pr-2", signClass(r.main.gross?.avg_ret_pct))}>{signed(r.main.gross?.avg_ret_pct)}</td>
                  <td className={cn("pr-2", signClass(r.main.standard?.avg_ret_pct))}>{signed(r.main.standard?.avg_ret_pct)}</td>
                  <td className={r.eligible ? "text-up" : "text-muted-foreground"}>{st.picks.includes(r.kind) ? "추천" : r.eligible ? "통과" : r.verdict}</td>
                </tr>))}</tbody>
            </table></div>
            <p className="mt-2 text-[11px] text-muted-foreground">통과 조건: 수수료 0.07% 기준 거래당 평균 이익 &gt; 0, 앞·뒤 절반 기간 모두 이익, 이익 난 종목 55% 이상, 거래 수 충분. 통과한 것 중 승률 순위와 연 수익률 순위의 평균으로 상위 4개를 추천. 연 수익률은 종목마다 같은 금액을 넣었을 때 기준 (복리 아님). 현재 지수 구성종목만 써서 생존 편향이 있고 세금은 빠져 있습니다.</p>
          </details>
        </>
      )}
    </Section>
  );
}

function toDraft(s: Strategy): Draft {
  return { id: s.id, name: s.name, kind: s.kind, interval: s.interval, symbols: s.symbols.join(", "), max: String(s.max_position_usd), flatten: s.flatten_at_close,
    params: Object.fromEntries(Object.entries(s.params).map(([a, b]) => [a, String(b)])) };
}

function BacktestView({ title, r }: { title: string; r: Backtest }) {
  const t = r.total;
  return (
    <div className="mt-4 rounded-lg border p-3">
      <div className="mb-2 text-sm font-medium">백테스트 · {title}{t && <span className="font-normal text-muted-foreground"> · {t.from.slice(0, 10)} ~ {t.to.slice(0, 10)} · 회당 ${t.notional.toLocaleString()}</span>}</div>
      {t ? (
        <>
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-5">
            {[["거래", `${t.trades}건`], ["승률 (비용 후)", isNum(t.win_rate) ? `${t.win_rate.toFixed(0)}%` : "—"],
              ["비용 전 손익", <span key="a" className={signClass(t.pl_no_cost)}>{usd(t.pl_no_cost, true)}</span>],
              ["수수료 + 슬리피지", <span key="b" className="text-down">−{usd(t.pl_no_cost - t.total_pl)}</span>],
              ["최종 손익", <span key="c" className={cn("font-semibold", signClass(t.total_pl))}>{usd(t.total_pl, true)}</span>]].map(([k, v]) => (
              <div key={String(k)} className="rounded-md border p-2"><div className="text-[11px] text-muted-foreground">{k}</div><div className="tabular">{v}</div></div>))}
          </div>
          {t.pl_no_cost > 0 && t.total_pl < 0 && <p className="mt-2 text-xs text-warn">비용이 없으면 이익인데 수수료 · 슬리피지 때문에 손실입니다 - 거래가 잦을수록 비용이 수익을 잡아먹습니다.</p>}
        </>
      ) : <p className="text-sm text-muted-foreground">결과 없음 (Data unavailable)</p>}
      <table className="mt-3 w-full text-xs tabular">
        <thead><tr className="border-b text-left text-muted-foreground">{["종목", "거래", r.interval === "1d" ? "연 거래" : "하루 거래", "승률", "비용 전", "최종 손익", "최대 낙폭", "보유만 했을 때"].map((h) => <th key={h} className="py-1 pr-2 font-normal">{h}</th>)}</tr></thead>
        <tbody>{r.rows.map((x) => x.error ? (
          <tr key={x.symbol} className="border-b"><td className="py-1 pr-2 font-mono">{x.symbol}</td><td colSpan={7} className="text-warn">{x.error}</td></tr>
        ) : (
          <tr key={x.symbol} className="border-b last:border-0">
            <td className="py-1 pr-2 font-mono">{x.symbol}</td><td className="pr-2">{x.trades}</td><td className="pr-2">{isNum(x.trades_per_day) ? (x.trades_per_day * (r.interval === "1d" ? 252 : 1)).toFixed(1) : "—"}</td>
            <td className="pr-2">{isNum(x.win_rate) ? `${x.win_rate.toFixed(0)}%` : "—"}</td><td className={cn("pr-2", signClass(x.pl_no_cost))}>{usd(x.pl_no_cost, true)}</td>
            <td className={cn("pr-2", signClass(x.total_pl))}>{usd(x.total_pl, true)}</td><td className="pr-2 text-down">{usd(x.max_dd)}</td><td className={signClass(x.buy_hold_pct)}>{pct(x.buy_hold_pct, 1)}</td>
          </tr>))}</tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted-foreground">{r.note} &lsquo;보유만 했을 때&rsquo;는 같은 기간 첫 봉 시가에 사서 끝까지 들고 있었을 때의 수익률입니다.</p>
    </div>
  );
}

function Positions({ d, onChange }: { d: Paper; onChange: (d: Paper) => void }) {
  const close = useMutation({ mutationFn: (id: number) => post<Paper>(`/api/paper/positions/${id}/close`), onSuccess: onChange, onError: (e: Error) => toast.error(e.message) });
  return (
    <Section title="보유 포지션 (모의)">
      {d.positions.length === 0 ? <p className="text-sm text-muted-foreground">보유 중인 포지션이 없습니다.</p> : (
        <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-sm tabular">
          <thead><tr className="border-b text-left text-[11px] text-muted-foreground">{["종목", "전략", "수량", "평단 (수수료 포함)", "현재가", "손절선", "평가 손익", ""].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}</tr></thead>
          <tbody>{d.positions.map((p) => (
            <tr key={p.id} className="border-b last:border-0">
              <td className="py-1.5 pr-3"><Link href={`/stock/${p.symbol}`} className="font-mono font-semibold hover:underline">{p.symbol}</Link></td>
              <td className="pr-3 text-xs">{p.strategy}</td><td className="pr-3">{p.qty.toFixed(4)}</td><td className="pr-3">${p.avg_price.toFixed(2)}</td>
              <td className="pr-3">{isNum(p.price) ? `$${p.price.toFixed(2)}` : <span className="text-warn text-xs">Data unavailable</span>}</td>
              <td className="pr-3">{isNum(p.stop_price) ? `$${p.stop_price.toFixed(2)}` : "—"}</td>
              <td className={cn("pr-3", signClass(p.pl))}>{usd(p.pl, true)} <span className="text-xs">({pct(p.pl_pct)})</span></td>
              <td><button onClick={() => close.mutate(p.id)} disabled={close.isPending} className="rounded-md border px-2 py-0.5 text-xs hover:bg-accent">청산</button></td>
            </tr>))}</tbody>
        </table></div>
      )}
    </Section>
  );
}

function ManualOrder({ onChange }: { onChange: (d: Paper) => void }) {
  const [f, setF] = useState({ symbol: "", side: "buy" as "buy" | "sell", amount: "500" });
  const go = useMutation({
    mutationFn: () => post<Paper>("/api/paper/orders", { symbol: f.symbol, side: f.side, ...(f.side === "buy" ? { notional_usd: Number(f.amount) } : {}) }),
    onSuccess: (d) => { onChange(d); toast.success("모의 주문 체결"); }, onError: (e: Error) => toast.error(e.message),
  });
  const input = "mt-1 h-8 rounded-md border bg-background px-2 text-sm";
  return (
    <Section title="수동 모의 주문" subtitle="현재가 기준으로 바로 가상 체결 (수수료 · 슬리피지 포함). 매도는 수동 보유분 전량.">
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); go.mutate(); }}>
        <label className="text-xs">종목<input required value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })} className={cn(input, "block w-28 font-mono")} placeholder="NVDA" /></label>
        <div className="text-xs">방향<div className="mt-1"><Segmented size="xs" ariaLabel="매수/매도" value={f.side} onChange={(v) => setF({ ...f, side: v })} options={[{ value: "buy", label: "매수" }, { value: "sell", label: "매도" }]} /></div></div>
        {f.side === "buy" && <label className="text-xs">금액 ($)<input value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} inputMode="decimal" className={cn(input, "block w-24")} /></label>}
        <button disabled={go.isPending} className="h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-60">모의 주문</button>
      </form>
    </Section>
  );
}

function Activity({ d }: { d: Paper }) {
  const [tab, setTab] = useState<"trades" | "events">("trades");
  const rows = useMemo(() => d.trades, [d.trades]);
  return (
    <Section title="기록" right={<Segmented size="xs" ariaLabel="기록 종류" value={tab} onChange={setTab} options={[{ value: "trades", label: `체결 ${d.trades.length}` }, { value: "events", label: `판단 로그 ${d.events.length}` }]} />}>
      {tab === "trades" ? (rows.length === 0 ? <p className="text-sm text-muted-foreground">체결 기록이 없습니다.</p> : (
        <div className="max-h-96 overflow-y-auto"><table className="w-full text-xs tabular">
          <thead className="sticky top-0 bg-card"><tr className="border-b text-left text-muted-foreground">{["시각 (KST)", "종목", "구분", "수량", "체결가", "수수료", "실현 손익", "전략 · 사유"].map((h) => <th key={h} className="py-1 pr-2 font-normal">{h}</th>)}</tr></thead>
          <tbody>{rows.map((t) => (
            <tr key={t.id} className="border-b last:border-0">
              <td className="py-1 pr-2">{kst(t.ts)}</td><td className="pr-2 font-mono">{t.symbol}</td>
              <td className={cn("pr-2", t.side === "buy" ? "text-up" : "text-down")}>{t.side === "buy" ? "매수" : "매도"}</td>
              <td className="pr-2">{t.qty.toFixed(4)}</td><td className="pr-2">${t.price.toFixed(2)}</td><td className="pr-2">${t.fee.toFixed(2)}</td>
              <td className={cn("pr-2", signClass(t.realized_pl))}>{t.realized_pl === null ? "" : usd(t.realized_pl, true)}</td>
              <td className="text-muted-foreground">{t.strategy} · {t.reason}</td>
            </tr>))}</tbody>
        </table></div>
      )) : (d.events.length === 0 ? <p className="text-sm text-muted-foreground">기록이 없습니다.</p> : (
        <ul className="max-h-96 space-y-1 overflow-y-auto text-xs">{d.events.map((e, i) => (
          <li key={i} className="flex gap-2"><span className="shrink-0 tabular text-muted-foreground">{kst(e.ts)}</span>
            <Pill tone={e.kind === "risk" ? "warn" : e.kind === "order" ? "primary" : "default"}>{{ risk: "위험관리", order: "주문", skip: "건너뜀" }[e.kind] ?? e.kind}</Pill>
            <span>{e.strategy ? `[${e.strategy}] ` : ""}{e.message}</span></li>))}</ul>
      ))}
    </Section>
  );
}
