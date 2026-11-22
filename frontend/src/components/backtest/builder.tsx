"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Check, CircleDashed, Play, Plus, Save, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { LineChart } from "@/components/charts/line-chart";
import { Pill, Section, Segmented, SkeletonBlock, Unavailable } from "@/components/common";
import { ApiError, api, del, post } from "@/lib/api";
import { ago } from "@/lib/format";
import { STATIC } from "@/lib/static";

import { BTN, BTN_GHOST, BenchTable, COLORS, type Curve, INPUT, type Metrics, MetricsGrid, RegimePanel, type Regimes, type Trade, TradesTable, usd } from "./shared";

// Strategy builder: combine entry / exit conditions from the backend catalog, name and save the rule,
// and backtest it right away. The condition list, parameter ranges and presets all come from /api/builder/catalog.

type Val = string | number;
interface ParamDef { key: string; label: string; kind: "select" | "int" | "float"; default: Val; options?: { value: string; label: string }[]; min?: number; max?: number; step?: number }
interface CondDef { type: string; side: "entry" | "exit"; name: string; help: string; params: ParamDef[]; needs_regime: boolean }
interface RiskDef { key: string; label: string; unit: string; min: number; max: number; help: string }
interface Cond { type: string; params: Record<string, Val> }
interface Side { mode: "any" | "all"; conditions: Cond[] }
interface Spec { entry: Side; exit: Side; risk: Record<string, Val> }
interface Preset { id: string; name: string; spec: Spec; description: string }
interface Catalog { conditions: CondDef[]; risk: RiskDef[]; presets: Preset[]; max_conditions: number; timing: string }
interface Saved { id: number; name: string; spec: Spec; note: string | null; description: string; updated_at: string | null }
interface StateSide { mode: "any" | "all"; items: { label: string; met: boolean; value: string | null }[]; met: boolean }
interface RunResult {
  symbol: string; strategy: { id: string; name: string; description: string; spec: Spec; risk: string[] }; metrics: Metrics; trades: Trade[];
  exit_reasons: Record<string, number>; equity: Curve; benchmarks: Record<string, { metrics: Metrics; equity: Curve }>;
  state: { as_of: string; entry: StateSide; exit: StateSide; action: "buy" | "sell" | "wait" } | null; regimes: Regimes | null;
  config: { start: string; end: string; commission_bps: number; slippage_bps: number }; disclaimer: string;
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const shortName = (n: string) => n.split(":")[0].trim();
const MODE = [{ value: "any" as const, label: "하나라도 충족" }, { value: "all" as const, label: "모두 충족" }];
const ACTION = {
  buy: { label: "매수 조건 충족", cls: "border-up/35 bg-up/[0.08] text-up" },
  sell: { label: "매도 조건 충족", cls: "border-down/35 bg-down/[0.08] text-down" },
  wait: { label: "대기 — 조건 미충족", cls: "border-border bg-muted/40 text-muted-foreground" },
};

/** The spec as sent to the backend: params in catalog order, blanks back to defaults, risk fields only when filled in. */
function normalise(spec: Spec, cat: Catalog): Spec {
  const side = (s: Side): Side => ({
    mode: s.mode,
    conditions: s.conditions.map((c) => {
      const def = cat.conditions.find((d) => d.type === c.type);
      if (!def) return c;
      return { type: c.type, params: Object.fromEntries(def.params.map((p) => {
        const v = c.params[p.key];
        return [p.key, p.kind === "select" ? String(v ?? p.default) : v === "" || v === undefined || Number.isNaN(Number(v)) ? p.default : Number(v)];
      })) };
    }),
  });
  const risk = Object.fromEntries(cat.risk.filter((r) => spec.risk[r.key] !== undefined && spec.risk[r.key] !== "" && Number(spec.risk[r.key]) > 0).map((r) => [r.key, Number(spec.risk[r.key])]));
  return { entry: side(spec.entry), exit: side(spec.exit), risk };
}

function describe(spec: Spec, cat: Catalog): string {
  const part = (s: Side) => s.conditions.map((c) => cat.conditions.find((d) => d.type === c.type)?.name ?? c.type).join(s.mode === "any" ? " 또는 " : " 그리고 ") || "조건 없음";
  return `매수: ${part(spec.entry)} / 매도: ${part(spec.exit)}`;
}

function SideEditor({ title, tone, side, defs, max, onChange }: { title: string; tone: "up" | "down"; side: Side; defs: CondDef[]; max: number; onChange: (s: Side) => void }) {
  const setCond = (i: number, c: Cond) => onChange({ ...side, conditions: side.conditions.map((x, j) => (j === i ? c : x)) });
  return (
    <div className="min-w-0 rounded-lg border p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className={cn("text-[13px] font-semibold", tone === "up" ? "text-up" : "text-down")}>{title}</h3>
        <Segmented size="xs" ariaLabel={`${title} 결합 방식`} value={side.mode} onChange={(m) => onChange({ ...side, mode: m })} options={MODE} />
      </div>
      <ul className="mt-3 space-y-2">
        {side.conditions.map((c, i) => {
          const def = defs.find((d) => d.type === c.type);
          return (
            <li key={`${c.type}-${i}`} className="rounded-md border bg-foreground/[0.02] px-3 py-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 text-[13px] font-medium">{def?.name ?? c.type}{def?.needs_regime && <Pill>매크로 국면 사용</Pill>}</div>
                  {def?.help && <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{def.help}</p>}
                </div>
                <button aria-label={`${def?.name ?? c.type} 삭제`} onClick={() => onChange({ ...side, conditions: side.conditions.filter((_, j) => j !== i) })}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-3.5" /></button>
              </div>
              {!!def?.params.length && (
                <div className="mt-2 flex flex-wrap items-end gap-2.5">
                  {def.params.map((p) => (
                    <label key={p.key} className="text-[11px] text-muted-foreground">{p.label}
                      {p.kind === "select" ? (
                        <select value={String(c.params[p.key] ?? p.default)} onChange={(e) => setCond(i, { ...c, params: { ...c.params, [p.key]: e.target.value } })}
                          className={cn(INPUT, "mt-1 block h-7 text-xs text-foreground")}>
                          {p.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      ) : (
                        <input type="number" inputMode="decimal" min={p.min} max={p.max} step={p.step ?? (p.kind === "int" ? 1 : 0.5)} value={String(c.params[p.key] ?? p.default)}
                          onChange={(e) => setCond(i, { ...c, params: { ...c.params, [p.key]: e.target.value === "" ? "" : Number(e.target.value) } })}
                          className={cn(INPUT, "mt-1 block h-7 w-[4.5rem] text-xs tabular text-foreground")} />
                      )}
                    </label>
                  ))}
                </div>
              )}
            </li>
          );
        })}
        {!side.conditions.length && <li className="rounded-md border border-dashed px-3 py-3 text-xs text-muted-foreground">조건이 없습니다. 아래에서 추가하세요.</li>}
      </ul>
      {side.conditions.length < max && (
        <label className="mt-2.5 flex items-center gap-1.5 text-xs text-muted-foreground"><Plus className="size-3.5" aria-hidden />
          <select value="" aria-label={`${title} 추가`} className={cn(INPUT, "h-7 flex-1 text-xs")} onChange={(e) => {
            const def = defs.find((d) => d.type === e.target.value);
            if (def) onChange({ ...side, conditions: [...side.conditions, { type: def.type, params: Object.fromEntries(def.params.map((p) => [p.key, p.default])) }] });
          }}>
            <option value="">조건 추가…</option>
            {defs.map((d) => <option key={d.type} value={d.type}>{d.name}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}

function StateList({ title, side }: { title: string; side: StateSide }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 text-xs font-medium">{title}<span className="text-[11px] font-normal text-muted-foreground">{side.mode === "any" ? "하나라도 충족하면" : "모두 충족해야"}</span>
        <Pill tone={side.met ? "primary" : "default"}>{side.met ? "충족" : "미충족"}</Pill></div>
      <ul className="mt-1.5 space-y-1">
        {side.items.map((it) => (
          <li key={it.label} className="flex items-start gap-1.5 text-xs">
            {it.met ? <Check className="mt-0.5 size-3.5 shrink-0 text-gold" aria-label="충족" /> : <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/60" aria-label="미충족" />}
            <span className={it.met ? "" : "text-muted-foreground"}>{it.label}{it.value && <span className="ml-1.5 tabular text-muted-foreground">· {it.value}</span>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function Builder({ ticker, start, end, cost }: { ticker: string; start: string; end: string; cost: { commission_bps: string; slippage_bps: string } }) {
  const qc = useQueryClient();
  const catalog = useQuery({ queryKey: ["builder-catalog"], queryFn: () => api<Catalog>("/api/builder/catalog"), staleTime: Infinity });
  const saved = useQuery({ queryKey: ["builder-strategies"], queryFn: () => api<{ items: Saved[]; max: number }>("/api/builder/strategies") });
  const cat = catalog.data;
  // until something is edited, the form shows the user's own rule (the first preset)
  const first = cat?.presets[0];
  const base = useMemo(() => (first ? clone(first.spec) : null), [first]);
  const [edited, setSpec] = useState<Spec | null>(null);
  const [named, setName] = useState<string | null>(null);
  const spec = edited ?? base;
  const name = named ?? (first ? shortName(first.name) : "내 규칙");

  const run = useMutation({
    mutationFn: (s: Spec) => post<RunResult>("/api/builder/run", { ticker, spec: s, name: name.trim() || "내 전략", start, end, commission_bps: Number(cost.commission_bps), slippage_bps: Number(cost.slippage_bps) }),
  });
  const save = useMutation({
    mutationFn: (s: Spec) => post<Saved>("/api/builder/strategies", { name: name.trim(), spec: s }),
    onSuccess: (s) => { toast.success(`'${s.name}' 저장됨`); qc.invalidateQueries({ queryKey: ["builder-strategies"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => del(`/api/builder/strategies/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["builder-strategies"] }),
    onError: (e) => toast.error((e as Error).message),
  });

  // first visit: show that rule's result straight away
  const booted = useRef(false);
  useEffect(() => {
    if (!cat || booted.current) return;
    booted.current = true;
    if (cat.presets[0]) run.mutate(normalise(cat.presets[0].spec, cat));
  }, [cat]); // eslint-disable-line react-hooks/exhaustive-deps

  const summary = useMemo(() => (spec && cat ? describe(spec, cat) : ""), [spec, cat]);
  if (catalog.isLoading) return <Section title="전략 빌더"><SkeletonBlock rows={8} /></Section>;
  if (!cat || !spec) return <Unavailable message={(catalog.error as Error | undefined)?.message} onRetry={() => catalog.refetch()} label="전략 빌더" />;

  const load = (s: Spec, n: string) => { setSpec(clone(s)); setName(n); run.mutate(normalise(s, cat)); };
  const ready = spec.entry.conditions.length > 0 && (spec.exit.conditions.length > 0 || Object.values(spec.risk).some((v) => Number(v) > 0));
  const r = run.data;

  return (
    <div className="space-y-4">
      <Section title="전략 빌더" subtitle={cat.timing}
        right={<span className="flex flex-wrap items-center gap-1.5">예시에서 시작{cat.presets.map((p) => (
          <button key={p.id} title={p.description || p.name} onClick={() => load(p.spec, shortName(p.name))} className={BTN_GHOST}>{shortName(p.name)}</button>))}</span>}>
        <div className="grid gap-3 lg:grid-cols-2">
          <SideEditor title="매수 조건" tone="up" side={spec.entry} defs={cat.conditions.filter((c) => c.side === "entry")} max={cat.max_conditions} onChange={(s) => setSpec({ ...spec, entry: s })} />
          <SideEditor title="매도 조건" tone="down" side={spec.exit} defs={cat.conditions.filter((c) => c.side === "exit")} max={cat.max_conditions} onChange={(s) => setSpec({ ...spec, exit: s })} />
        </div>
        <div className="mt-3 rounded-lg border p-3.5">
          <h3 className="text-[13px] font-semibold">위험 관리 <span className="text-[11px] font-normal text-muted-foreground">선택 · 비워 두면 사용하지 않음</span></h3>
          <div className="mt-2 flex flex-wrap items-end gap-3">
            {cat.risk.map((f) => (
              <label key={f.key} title={f.help} className="text-[11px] text-muted-foreground">{f.label} ({f.unit})
                <input type="number" inputMode="decimal" min={f.min} max={f.max} placeholder="—" value={String(spec.risk[f.key] ?? "")}
                  onChange={(e) => setSpec({ ...spec, risk: { ...spec.risk, [f.key]: e.target.value } })} className={cn(INPUT, "mt-1 block h-7 w-24 text-xs tabular text-foreground")} />
              </label>
            ))}
          </div>
        </div>
        <p className="mt-3 rounded-md bg-muted/30 px-3 py-2 text-xs leading-relaxed"><span className="text-muted-foreground">이 전략은 · </span>{summary}</p>
        <div className="mt-3 flex flex-wrap items-end gap-2.5">
          <label className="text-xs">전략 이름<input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} className={cn(INPUT, "mt-1 block w-56")} /></label>
          <button onClick={() => run.mutate(normalise(spec, cat))} disabled={run.isPending || !ready} className={BTN}><Play className="size-4" />{ticker} 백테스트</button>
          <button onClick={() => save.mutate(normalise(spec, cat))} disabled={STATIC || save.isPending || !ready || !name.trim()} className={cn(BTN_GHOST, "h-8")}><Save className="size-3.5" />저장</button>
          {!ready && <span className="text-[11px] text-warn">매수 조건 1개 이상, 그리고 매도 조건이나 위험 관리 중 하나가 필요합니다</span>}
          {STATIC && <span className="text-[11px] text-muted-foreground">데모에는 예시 전략의 결과만 저장돼 있습니다 — 조건을 바꾼 실행과 저장은 실제 앱에서 동작합니다</span>}
        </div>
        {!STATIC && !!saved.data?.items.length && (
          <div className="mt-4 border-t pt-3">
            <h3 className="text-xs font-medium text-muted-foreground">저장한 전략 ({saved.data.items.length}/{saved.data.max})</h3>
            <ul className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {saved.data.items.map((s) => (
                <li key={s.id} className="flex items-start justify-between gap-2 rounded-md border px-3 py-2">
                  <button onClick={() => load(s.spec, s.name)} className="min-w-0 text-left">
                    <div className="truncate text-[13px] font-medium hover:text-gold">{s.name}</div>
                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">{s.description}</p>
                    <p className="mt-0.5 text-[10.5px] text-muted-foreground/70">{ago(s.updated_at)} 저장</p>
                  </button>
                  <button aria-label={`${s.name} 삭제`} onClick={() => { if (window.confirm(`'${s.name}' 전략을 삭제할까요?`)) remove.mutate(s.id); }}
                    className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-down"><Trash2 className="size-3.5" /></button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      {run.isPending ? <Section title="결과"><SkeletonBlock rows={8} /></Section> : run.isError ? (
        <Unavailable label="전략 백테스트" onRetry={() => run.mutate(normalise(spec, cat))}
          message={(run.error as ApiError).code === "static" ? "데모에는 예시 전략 × 대표 종목의 결과만 저장돼 있습니다. 다른 조건이나 종목은 실제 앱에서 실행됩니다." : (run.error as Error).message} />
      ) : r && (
        <>
          {r.state && (
            <Section title={`지금 상태 · ${r.symbol}`} subtitle={`${r.state.as_of} 종가 기준 · 오늘 이 규칙을 적용하면`}
              right={<span className={cn("rounded-full border px-3 py-1 text-xs font-semibold", ACTION[r.state.action].cls)}>{ACTION[r.state.action].label}</span>}>
              <div className="grid gap-4 md:grid-cols-2">
                <StateList title="매수 조건" side={r.state.entry} />
                <StateList title="매도 조건" side={r.state.exit} />
              </div>
            </Section>
          )}
          <Section title={`${r.symbol} · ${r.strategy.name}`}
            subtitle={<>{r.metrics.start} → {r.metrics.end} · 비용 편도 {r.config.commission_bps}+{r.config.slippage_bps}bp<br />{r.strategy.description}</>}
            right={<>{r.strategy.risk.map((x) => <Pill key={x} tone="warn">{x}</Pill>)}</>}>
            <MetricsGrid m={r.metrics} />
            {!!Object.keys(r.exit_reasons).length && (
              <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">청산 사유
                {Object.entries(r.exit_reasons).map(([k, n]) => <Pill key={k}>{k} {n}건</Pill>)}</div>
            )}
          </Section>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <Section title="자산 곡선 vs 벤치마크" subtitle="$10,000로 시작 · 벤치마크는 같은 기간 단순 보유 (지수는 가격 수익률)">
              <LineChart height={320} format={usd} lines={[{ name: "전략", color: COLORS[0], points: r.equity, width: 2 },
                ...Object.entries(r.benchmarks).map(([k, v], i) => ({ name: k, color: COLORS[i + 1], points: v.equity, width: 1 as const }))]} />
            </Section>
            <Section title="전략 vs 벤치마크">
              <BenchTable highlight="전략" rows={[["전략", r.metrics] as const, ...Object.entries(r.benchmarks).map(([k, v]) => [k, v.metrics] as const)]} />
              <p className="mt-3 text-[11px] text-muted-foreground">{r.disclaimer}</p>
            </Section>
          </div>
          <RegimePanel r={r.regimes} />
          <Section title={`거래 내역 (${r.trades.length}건)`}><TradesTable trades={r.trades} /></Section>
        </>
      )}
    </div>
  );
}
