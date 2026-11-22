"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { BarChart3, Eye, Globe2, Scale } from "lucide-react";

import { QueryState, Section, SkeletonBlock, scoreTone } from "@/components/common";
import { api } from "@/lib/api";
import { viewKo } from "@/lib/i18n";

interface HorizonOutlook {
  key: "short" | "medium" | "long"; title: string; range: string; score: number; view: string; headline: string;
  technical: string[]; macro: string[]; conclusion: string; stance: "positive" | "mixed" | "neutral" | "negative"; watch: string[];
  context: number | null; context_label: string; context_formula: string;
}
interface Outlook { symbol: string; as_of: string; horizons: Record<string, HorizonOutlook>; method: string; updated_at: string }

const STANCE: Record<HorizonOutlook["stance"], { label: string; cls: string }> = {
  positive: { label: "우호적", cls: "border-up/40 bg-up/10 text-up" },
  mixed: { label: "엇갈림", cls: "border-warn/40 bg-warn/10 text-warn" },
  neutral: { label: "중립", cls: "border-border bg-muted text-muted-foreground" },
  negative: { label: "부정적", cls: "border-down/40 bg-down/10 text-down" },
};

export const useOutlook = (t: string) =>
  useQuery({ queryKey: ["outlook", t], queryFn: () => api<Outlook>(`/api/stock/${encodeURIComponent(t)}/outlook`), staleTime: 180_000 });

function Block({ icon: Icon, title, items }: { icon: typeof Globe2; title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground"><Icon className="size-3.5" />{title}</div>
      <ul className="space-y-1 text-[13px] leading-relaxed">{items.map((x, i) => <li key={i} className="flex gap-1.5"><span className="mt-2 size-1 shrink-0 rounded-full bg-foreground/40" aria-hidden />{x}</li>)}</ul>
    </div>
  );
}

/** 단기 · 중기 · 장기 written analysis (technical + macro), shown right under the price chart. */
export function OutlookPanel({ symbol }: { symbol: string }) {
  const q = useOutlook(symbol);
  return (
    <Section title="기간별 종합 분석 · 기술적 + 매크로" subtitle={q.data ? `${q.data.as_of} 종가 기준 · ${q.data.method}` : undefined}>
      <QueryState q={q} label="기간별 분석" skeleton={<div className="grid gap-3 lg:grid-cols-3">{[0, 1, 2].map((i) => <SkeletonBlock key={i} rows={7} />)}</div>}>
        {(d) => (
          <div className="grid gap-3 lg:grid-cols-3">
            {(["short", "medium", "long"] as const).map((k) => {
              const h = d.horizons[k];
              if (!h) return null;
              const st = STANCE[h.stance];
              return (
                <article key={k} className="flex flex-col gap-3 rounded-lg border p-3">
                  <header className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold">{h.title} 분석 <span className="font-normal text-muted-foreground">{h.range}</span></h3>
                      <p className="text-[11px] text-muted-foreground">기술 <b className={cn("tabular", scoreTone(h.score))}>{h.score.toFixed(0)}</b> ({viewKo(h.view)})
                        {" · "}<span title={h.context_formula}>{h.context_label} <b className={cn("tabular", scoreTone(h.context))}>{h.context === null ? "—" : h.context.toFixed(0)}</b></span></p>
                    </div>
                    <span className={cn("shrink-0 whitespace-nowrap rounded-md border px-2 py-0.5 text-xs font-semibold", st.cls)}>{st.label}</span>
                  </header>
                  <Block icon={BarChart3} title="기술적 분석" items={h.technical} />
                  <Block icon={Globe2} title={k === "short" ? "매크로 · 시장 심리" : k === "medium" ? "매크로 · 업종" : "매크로 · 펀더멘털"} items={h.macro} />
                  <div className="rounded-md bg-muted/60 p-2.5">
                    <div className="mb-0.5 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground"><Scale className="size-3.5" />종합 판단</div>
                    <p className="text-[13px] leading-relaxed">{h.conclusion}</p>
                  </div>
                  {h.watch.length > 0 && (
                    <div className="mt-auto text-xs">
                      <div className="mb-0.5 flex items-center gap-1.5 font-semibold text-muted-foreground"><Eye className="size-3.5" />체크포인트</div>
                      <ul className="flex flex-wrap gap-1">{h.watch.map((w) => <li key={w} className="rounded border px-1.5 py-0.5">{w}</li>)}</ul>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </QueryState>
    </Section>
  );
}
