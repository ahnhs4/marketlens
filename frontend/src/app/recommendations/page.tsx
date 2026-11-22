"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { useState } from "react";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock, scoreTone } from "@/components/common";
import { ExcludedNote } from "@/components/excluded-note";
import { RegimeBadge } from "@/components/market";
import { useSettings } from "@/components/providers";
import { COLS, RankTable } from "@/components/rank-table";
import { Slider } from "@/components/ui/slider";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { groupKo, kindKo, scanStateKo, universeKo } from "@/lib/i18n";
import type { Regime, ScanMeta, ScanRow } from "@/lib/types";

const UNIVERSES = ["ALL", "SP500", "NDX", "KOSPI", "KOSDAQ", "WATCHLIST"] as const;
type Uni = (typeof UNIVERSES)[number];
const FACTOR_KO: Record<string, string> = { rates_down: "금리", curve_steepening: "장단기 금리차", usd_down: "달러", oil_up: "유가", growth: "성장", inflation: "인플레이션", liquidity: "유동성", risk: "위험선호" };

interface MacroRank extends ScanMeta {
  regime: Pick<Regime, "label" | "score" | "quadrant" | "overlays" | "subscores">;
  factors: Record<string, { value: number; text: string }>;
  groups: { group: string; kind: string; proxy: string; score: number; perf_3m: number | null; reasons: string[]; risks: string[]; contributions: { factor: string; move: string; points: number; evidence: string; exposure: number }[] }[];
  stocks: { symbol: string; name: string; group: string; macro: number; technical: number; reasons: string[]; risks: string[] }[];
  method: string;
}

function ScanNote({ m }: { m?: ScanMeta }) {
  if (!m) return null;
  return <span className="text-[11px] text-muted-foreground">스캔: {scanStateKo(m.scan.state)}{m.scan.state === "running" ? ` ${m.scan.processed}/${m.scan.total}` : ""}{m.scan.finished_at ? ` · ${ago(m.scan.finished_at)}` : ""} · {m.results}종목</span>;
}

export default function RecommendationsPage() {
  const [uni, setUni] = useState<Uni>("ALL");
  const { settings } = useSettings();
  const [wTech, setWTech] = useState<number | null>(null);
  const wt = wTech ?? settings?.weights.combined_ranking.technical ?? 0.6;
  const macro = useQuery({ queryKey: ["reco", "macro"], queryFn: () => api<MacroRank>("/api/recommendations/macro"), staleTime: 300_000 });
  const tech = useQuery({ queryKey: ["reco", "tech", uni], queryFn: () => api<ScanMeta & { items: ScanRow[]; count: number }>(`/api/recommendations/technical?universe=${uni}&limit=50`), staleTime: 120_000 });
  const comb = useQuery({ queryKey: ["reco", "comb", uni, wt], queryFn: () => api<ScanMeta & { items: ScanRow[]; overlap: ScanRow[]; weights: Record<string, number> }>(`/api/recommendations/combined?universe=${uni}&limit=50&w_tech=${wt}&w_macro=${(1 - wt).toFixed(2)}`), staleTime: 120_000 });
  const [kind, setKind] = useState("All");

  return (
    <div className="space-y-5">
      <PageHeader title="투자 기회" description="매크로 환경과 차트 구조가 함께 맞아떨어지는 곳. 모든 점수는 구성 요소까지 공개되며, 예측이 아닙니다."
        right={<Segmented ariaLabel="종목군" value={uni} onChange={setUni} options={UNIVERSES.map((u) => ({ value: u, label: universeKo(u) }))} />} />
      <Tabs defaultValue="combined">
        <TabsList>
          <TabsTrigger value="combined">매크로 + 기술적</TabsTrigger>
          <TabsTrigger value="macro">매크로 순위</TabsTrigger>
          <TabsTrigger value="technical">기술적 순위</TabsTrigger>
        </TabsList>

        <TabsContent value="combined" className="mt-3 space-y-4">
          <Section title="종합 순위" subtitle="종합 = w × 기술적 + (1 − w) × 매크로"
            right={<div className="flex items-center gap-3">
              <span className="text-xs">기술적 {Math.round(wt * 100)}% / 매크로 {Math.round((1 - wt) * 100)}%</span>
              <div className="w-40"><Slider value={[wt * 100]} min={0} max={100} step={5} onValueChange={(v) => setWTech(((Array.isArray(v) ? v[0] : v) as number) / 100)} /></div>
              <ScanNote m={comb.data} />
            </div>}>
            <QueryState q={comb} label="종합 순위" skeleton={<SkeletonBlock rows={10} />}>
              {(d) => (
                <>
                  {d.note && <p className="mb-2 text-sm text-warn">{d.note}</p>}
                  <ExcludedNote e={d.excluded_overbought} />
                  {d.overlap.length > 0 && (
                    <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="text-muted-foreground">둘 다 강함 (기술 ≥ 62 · 매크로 ≥ 60):</span>
                      {d.overlap.map((o) => <a key={o.symbol} href={`/stock/${o.symbol}`} className="rounded border border-up/30 px-1.5 py-0.5 font-mono text-up hover:bg-up/10">{o.symbol}</a>)}
                    </div>
                  )}
                  <RankTable rows={d.items} initialSort="combined"
                    cols={[COLS.symbol, COLS.combined, COLS.score, COLS.macro, COLS.rating, COLS.signal, COLS.trend, COLS.risk, COLS.reason]} />
                </>
              )}
            </QueryState>
          </Section>
        </TabsContent>

        <TabsContent value="macro" className="mt-3 space-y-4">
          <QueryState q={macro} label="매크로 순위" skeleton={<SkeletonBlock rows={10} />}>
            {(m) => (
              <>
                <ExcludedNote e={m.excluded_overbought} />
                <Section title="현재 매크로 환경" right={<RegimeBadge r={m.regime} />}>
                  <div className="flex flex-wrap gap-2 text-xs">
                    {Object.entries(m.factors).map(([k, f]) => (
                      <Pill key={k} tone={f.value > 0.15 ? "up" : f.value < -0.15 ? "down" : "default"}>{FACTOR_KO[k] ?? k}: {f.text}</Pill>
                    ))}
                  </div>
                  <p className="mt-2 text-[11px] text-muted-foreground">{m.method}. 민감도 표는 README → 점수 산출 방법에 정리되어 있습니다.</p>
                </Section>
                <Section title="매크로 기회 순위" right={<Segmented size="xs" value={kind} onChange={setKind} options={["All", "Sector", "Industry", "Country", "Asset"].map((k) => ({ value: k, label: k === "All" ? "전체" : kindKo(k) }))} />}>
                  <ol className="divide-y">
                    {m.groups.filter((g) => kind === "All" || g.kind === kind).map((g, i) => (
                      <li key={g.group} className="grid gap-2 py-2.5 md:grid-cols-[28px_200px_60px_1fr]">
                        <span className="text-xs text-muted-foreground tabular">{i + 1}</span>
                        <span><b className="text-sm">{groupKo(g.group)}</b> <span className="text-[11px] text-muted-foreground">{kindKo(g.kind)} · {g.proxy}{g.perf_3m !== null ? ` · 3개월 ${g.perf_3m > 0 ? "+" : ""}${g.perf_3m.toFixed(1)}%` : ""}</span></span>
                        <span className={cn("text-lg font-semibold tabular", scoreTone(g.score))}>{g.score.toFixed(0)}</span>
                        <div className="text-xs">
                          {g.reasons.slice(0, 3).map((r) => <div key={r} className="text-up">+ {r}</div>)}
                          {g.risks.slice(0, 2).map((r) => <div key={r} className="text-down">− {r}</div>)}
                        </div>
                      </li>
                    ))}
                  </ol>
                </Section>
                <Section title="매크로 정합도가 가장 높은 종목" subtitle="업종 노출도 + 실측 팩터 민감도 (최신 스캔)">
                  <ul className="grid gap-2 md:grid-cols-2">
                    {m.stocks.map((s) => (
                      <li key={s.symbol} className="rounded-lg border p-2.5 text-xs">
                        <div className="flex justify-between"><a href={`/stock/${s.symbol}`} className="font-mono text-sm font-semibold hover:underline">{s.symbol}</a><span>매크로 <b className={scoreTone(s.macro)}>{s.macro.toFixed(0)}</b> · 기술 {s.technical.toFixed(0)}</span></div>
                        <div className="text-muted-foreground">{s.name} · {s.group ? groupKo(s.group) : "—"}</div>
                        {s.reasons.slice(0, 2).map((r) => <div key={r} className="mt-0.5">+ {r}</div>)}
                        {s.risks.slice(0, 1).map((r) => <div key={r} className="mt-0.5 text-down">− {r}</div>)}
                      </li>
                    ))}
                  </ul>
                </Section>
              </>
            )}
          </QueryState>
        </TabsContent>

        <TabsContent value="technical" className="mt-3">
          <Section title="기술적 기회 순위" right={<ScanNote m={tech.data} />}>
            <QueryState q={tech} label="기술적 순위" skeleton={<SkeletonBlock rows={10} />}>
              {(d) => <><ExcludedNote e={d.excluded_overbought} /><RankTable rows={d.items} initialSort="technical" cols={[COLS.symbol, COLS.score, COLS.signal, COLS.change, COLS.trend, COLS.mom, COLS.rsi, COLS.vol, COLS.pattern, COLS.risk, COLS.reason]} /></>}
            </QueryState>
          </Section>
        </TabsContent>
      </Tabs>
      <p className="text-[11px] text-muted-foreground">* 나스닥100은 나스닥 상장 시가총액 상위 100개 종목으로 근사합니다. 순위는 종목 스캔 때마다 갱신됩니다 (장중 30분마다).</p>
    </div>
  );
}
