"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowDownRight, ArrowUpRight, Info, Minus } from "lucide-react";
import { useState } from "react";

import { LineChart } from "@/components/charts/line-chart";
import { FedWatchCard } from "@/components/fedwatch";
import { PageHeader, Pill, QueryState, Section, SkeletonBlock, Sparkline, UpdatedAt } from "@/components/common";
import { RegimeBadge } from "@/components/market";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { num } from "@/lib/format";
import { useRegime } from "@/lib/hooks";
import { macroGroupKo, overlayKo, quadrantKo } from "@/lib/i18n";
import type { MacroCard, Regime } from "@/lib/types";

const GROUPS = ["Rates", "Inflation", "Currencies", "Commodities", "Liquidity", "Economy", "Sentiment"];
const SUBSCORE_KO: Record<string, string> = { growth: "경기", inflation: "물가", liquidity: "유동성", policy: "통화정책·금리", risk: "위험선호" };

function fmt(c: MacroCard, v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  const d = c.decimals ?? 2;
  const s = num(v, d);
  if (c.unit === "%" || c.unit === "pp") return `${s}${c.unit === "%" ? "%" : "pp"}`;
  if (c.unit === "$") return `$${s}`;
  if (c.unit === "$T") return `$${s}조`;
  if (c.unit === "$B") return `$${s}십억`;
  if (c.unit === "k") return `${s}천`;
  if (c.unit === "₩" || c.unit === "¥") return `${c.unit}${s}`;
  return s;
}

function RegimeHero({ r }: { r: Regime }) {
  const probs = Object.entries(r.quadrant_probabilities).sort((a, b) => b[1] - a[1]);
  return (
    <Section title="매크로 국면" subtitle={r.formula} right={<UpdatedAt at={r.updated_at} source={`FRED (${r.fred_mode}) + Yahoo`} />}>
      <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4">
          <RegimeBadge r={r} size="lg" />
          <div className="space-y-1.5">
            {Object.entries(r.subscores).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[96px_1fr_36px] items-center gap-2 text-xs">
                <span className="text-muted-foreground">{SUBSCORE_KO[k] ?? k}</span>
                <div className="relative h-1.5 rounded-full bg-muted">
                  <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                  <div className={cn("absolute inset-y-0 rounded-full", v >= 50 ? "left-1/2 bg-up-mark" : "right-1/2 bg-down-mark")} style={{ width: `${Math.abs(v - 50)}%` }} />
                </div>
                <span className="text-right tabular">{v.toFixed(0)}</span>
              </div>
            ))}
            <p className="text-[10px] text-muted-foreground">물가 점수가 50을 넘으면 물가 압력 상승을 뜻하며, 종합 점수에는 역풍으로 반영됩니다.</p>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold">경기 사이클 4분면</div>
            {probs.map(([k, v]) => (
              <div key={k} className="grid grid-cols-[190px_1fr_40px] items-center gap-2 text-xs">
                <span className={k === r.quadrant ? "font-semibold" : "text-muted-foreground"}>{quadrantKo(k)}</span>
                <div className="h-1.5 rounded-full bg-muted"><div className="h-full rounded-full bg-s1" style={{ width: `${v * 100}%` }} /></div>
                <span className="text-right tabular">{(v * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
          {r.overlays.length > 0 && <div className="flex flex-wrap gap-1.5">{r.overlays.map((o) => <Pill key={o.name} tone="primary">{overlayKo(o.name)}<span className="text-muted-foreground"> · {o.reason}</span></Pill>)}</div>}
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold">근거 - 종합 점수에 가장 크게 기여한 요인 (점수)</div>
          <ul className="space-y-1.5">
            {r.reasons.map((x) => (
              <li key={x.text} className="flex items-start gap-2 text-sm">
                <span className={cn("w-12 shrink-0 text-right text-xs font-semibold tabular", x.points > 0 ? "text-up" : "text-down")}>{x.points > 0 ? "+" : ""}{x.points.toFixed(1)}</span>
                {x.points > 0 ? <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-up" /> : <ArrowDownRight className="mt-0.5 size-4 shrink-0 text-down" />}
                <span>{x.text}<span className="ml-1.5 text-[10px] text-muted-foreground">{SUBSCORE_KO[x.category] ?? x.category}</span></span>
              </li>
            ))}
          </ul>
          <p className="mt-3 flex gap-1.5 text-[11px] text-muted-foreground"><Info className="mt-0.5 size-3 shrink-0" />{r.notes.join(" ")}</p>
        </div>
      </div>
    </Section>
  );
}

function CardView({ c, onOpen }: { c: MacroCard; onOpen: () => void }) {
  if (c.status) return <div className="rounded-xl border bg-card p-3.5 shadow-card text-xs"><div className="font-medium">{c.label}</div><p className="mt-2 text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable)</p></div>;
  const Icon = c.direction === "up" ? ArrowUpRight : c.direction === "down" ? ArrowDownRight : Minus;
  const eff = c.equity_effect;
  return (
    <button onClick={onOpen} className="rounded-xl border bg-card p-3.5 shadow-card text-left transition-colors hover:border-gold/35">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium">{c.label}</span>
        <span className="text-[10px] text-muted-foreground">{c.date}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-lg font-semibold tabular">{fmt(c, c.value)}</span>
        <span className={cn("inline-flex items-center text-xs tabular", eff === "positive" ? "text-up" : eff === "negative" ? "text-down" : "text-muted-foreground")}>
          <Icon className="size-3.5" />{c.change !== null && c.change !== undefined && Math.abs(c.change) >= 0.5 * 10 ** -(c.decimals ?? 2) ? `${c.change > 0 ? "+" : ""}${num(c.change, c.decimals ?? 2)}` : "변화 없음"}
        </span>
      </div>
      <div className="text-[10px] text-muted-foreground">{c.previous_label}: {fmt(c, c.previous)} · {c.previous2_label}: {fmt(c, c.previous2)}</div>
      <Sparkline data={c.sparkline ?? []} className="mt-1 h-7" positive={eff === "positive" || (eff !== "negative" && c.direction !== "down")} />
      {c.percentile_5y !== null && c.percentile_5y !== undefined && (
        <div className="mt-1">
          <div className="relative h-1 rounded-full bg-muted"><div className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground" style={{ left: `${c.percentile_5y}%` }} /></div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">5년 중 백분위 {c.percentile_5y.toFixed(0)} · 5년 평균 {fmt(c, c.avg_5y)}</div>
        </div>
      )}
      <p className={cn("mt-1 text-[11px]", eff === "positive" ? "text-up" : eff === "negative" ? "text-down" : "text-muted-foreground")}>
        {c.direction === "up" ? "상승 시: " : c.direction === "down" ? "하락 시: " : ""}{c.impact}
      </p>
    </button>
  );
}

export default function MacroPage() {
  const reg = useRegime();
  const cards = useQuery({ queryKey: ["macro-cards"], queryFn: () => api<{ cards: MacroCard[]; unavailable: { key: string; label: string; group: string; reason: string }[]; updated_at: string; meta: { fred_mode: string; missing: string[] } }>("/api/macro/overview"), staleTime: 300_000 });
  const [open, setOpen] = useState<MacroCard | null>(null);
  const hist = useQuery({
    queryKey: ["macro-series", open?.key], enabled: !!open,
    queryFn: () => api<{ label: string; unit: string; points: { date: string; value: number }[] }>(`/api/macro/series/${open!.key}?years=10`),
  });
  return (
    <div className="space-y-5">
      <PageHeader title="매크로 환경" description="금리 · 물가 · 유동성 · 경기 · 투자심리를 종합해 설명 가능한 시장 국면으로 정리하고, 사이트의 모든 신호에 반영합니다." />
      <QueryState q={reg} label="시장 국면" skeleton={<Section title="매크로 국면"><SkeletonBlock rows={8} /></Section>}>{(r) => <RegimeHero r={r} />}</QueryState>
      <FedWatchCard />
      <QueryState q={cards} label="매크로 지표" skeleton={<SkeletonBlock rows={10} />}>
        {(d) => (
          <div className="space-y-4">
            {GROUPS.map((g) => {
              const cs = d.cards.filter((c) => c.group === g);
              const un = d.unavailable.filter((u) => u.group === g);
              if (!cs.length && !un.length) return null;
              return (
                <Section key={g} title={macroGroupKo(g)} right={<span>{cs[0]?.source ?? ""}</span>} bodyClassName="p-3">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-6">
                    {cs.map((c) => <CardView key={c.key} c={c} onOpen={() => setOpen(c)} />)}
                    {un.map((u) => (
                      <div key={u.key} className="rounded-lg border border-dashed p-3 text-xs">
                        <div className="font-medium">{u.label}</div>
                        <p className="mt-1 text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable) - {u.reason}</p>
                      </div>
                    ))}
                  </div>
                </Section>
              );
            })}
            <p className="text-[11px] text-muted-foreground">FRED 연결 방식: {d.meta.fred_mode === "api" ? "공식 API (FRED_API_KEY 사용)" : "공개 CSV (공식 API를 쓰려면 FRED_API_KEY 설정)"}. 날짜는 각 지표의 최신 관측일이며, 월간 지표는 발표 시차가 있습니다.</p>
          </div>
        )}
      </QueryState>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="sm:max-w-3xl">
          <DialogTitle>{open?.label}</DialogTitle>
          {open?.impact && <p className="text-xs text-muted-foreground">일반적인 시장 영향: {open.impact}</p>}
          <QueryState q={hist} label="과거 추이">
            {(h) => <LineChart lines={[{ name: h.label, color: "--s1", points: h.points }]} area height={300} />}
          </QueryState>
        </DialogContent>
      </Dialog>
    </div>
  );
}
