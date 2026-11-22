"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Activity, AlertTriangle, ArrowRight, Bell, Landmark, Newspaper, Sparkles, Target, TrendingUp } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { ImpactBadge, QueryState, Section, SentimentBadge, SkeletonBlock, UpdatedAt } from "@/components/common";
import { ExcludedNote } from "@/components/excluded-note";
import { FearGreedPanel } from "@/components/fear-greed";
import { FedWatchTile } from "@/components/fedwatch";
import { RecapCard } from "@/components/recap";
import { OpportunityList, RegimeBadge, SnapshotCards } from "@/components/market";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ago, timeKST } from "@/lib/format";
import { useSnapshot } from "@/lib/hooks";
import { groupKo, kindKo, overlayKo, quadrantKo, scanStateKo, signalKo } from "@/lib/i18n";
import type { Regime, ScanMeta, ScanRow } from "@/lib/types";

interface Dashboard {
  regime: Pick<Regime, "label" | "score" | "quadrant" | "overlays" | "subscores"> | null;
  top_macro_signal: string | null;
  top_risk: string | null;
  top_sector: { group: string; score: number; kind: string } | null;
  top_setup: ScanRow | null;
  important_news_count: number;
  important_news: { title: string; source: string; impact: string; sentiment: string; published_at: string; url: string }[];
  watchlist_alerts: number;
  since_last_visit: { symbol: string; from: number; to: number; change: number; signal: [string, string]; from_date: string; to_date: string; drivers: string[] }[];
  last_visit: string | null;
  breadth: { score: number; label: string; pct_above_sma200: number } | null;
  scan: ScanMeta["scan"];
  updated_at: string;
}

interface Briefing {
  date: string; generated_at: string; method: string;
  regime: { label: string; score: number; quadrant: string; overlays: { name: string }[] } | null;
  market: string[]; macro: string[]; interpretation: string[];
  news: { title: string; source: string; published_at: string; impact: string; sentiment: string; url: string }[];
  strong_sectors: string[]; weak_sectors: string[];
  top_setups: { symbol: string; name: string; score: number; signal: string; reason: string[] }[];
  risks: string[];
  watchlist_changes: Dashboard["since_last_visit"];
}

interface Top extends ScanMeta {
  technical: ScanRow[]; combined: ScanRow[];
  macro_groups: { group: string; kind: string; score: number; reasons: string[]; risks: string[] }[];
  macro_stocks: { symbol: string; name: string; group: string; macro: number; technical: number; reasons: string[]; risks: string[] }[];
}

function KeyTile({ icon: Icon, label, children, href }: { icon: typeof Activity; label: string; children: ReactNode; href?: string }) {
  const body = (
    <div className="flex h-full flex-col gap-1 rounded-xl border bg-card p-3.5 shadow-card transition-colors hover:border-gold/35">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-muted-foreground"><Icon className="size-3.5" />{label}</div>
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
  return href ? <Link href={href} className="block h-full">{body}</Link> : body;
}

function BriefList({ title, items, tone }: { title: string; items: string[]; tone?: "up" | "down" }) {
  if (!items?.length) return null;
  return (
    <div>
      <h3 className={cn("mb-1 text-xs font-semibold uppercase tracking-wide", tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-muted-foreground")}>{title}</h3>
      <ul className="space-y-1 text-sm">{items.map((t, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-foreground/50" aria-hidden />{t}</li>)}</ul>
    </div>
  );
}

export default function Home() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/dashboard"), refetchInterval: 120_000 });
  const snap = useSnapshot();
  const brief = useQuery({ queryKey: ["briefing"], queryFn: () => api<Briefing>("/api/briefing"), staleTime: 300_000 });
  const top = useQuery({ queryKey: ["reco", "top"], queryFn: () => api<Top>("/api/recommendations/top"), staleTime: 300_000 });
  const d = dash.data;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4 pb-2">
        <div>
          <div className="text-[11px] font-medium tracking-[0.2em] text-gold">
            {new Date().toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long", timeZone: "Asia/Seoul" })}
          </div>
          <h1 className="mt-2 text-[30px] font-semibold leading-tight tracking-[-0.03em]">마켓 대시보드</h1>
          <div className="mt-2.5 h-px w-12 bg-gradient-to-r from-gold to-transparent" aria-hidden />
          <p className="mt-2.5 text-[13.5px] text-muted-foreground">한국 시간 기준{d?.last_visit ? ` · 마지막 방문 ${ago(d.last_visit)}` : ""}</p>
        </div>
        <span className="text-xs text-muted-foreground"><UpdatedAt at={d?.updated_at} /></span>
      </div>

      <RecapCard />

      {dash.isLoading ? <div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-24 rounded-xl border bg-card p-3.5 shadow-card"><SkeletonBlock rows={2} /></div>)}</div> : (
        <QueryState q={dash} label="대시보드">
          {(d) => (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">
              <KeyTile icon={Activity} label="시장 국면" href="/macro">
                {d.regime ? <><RegimeBadge r={d.regime} /><div className="mt-1 text-xs text-muted-foreground">{quadrantKo(d.regime.quadrant)}{d.regime.overlays.length ? ` · ${d.regime.overlays.map((o) => overlayKo(o.name)).join(", ")}` : ""}</div></> : "데이터를 불러올 수 없습니다 (Data unavailable)"}
              </KeyTile>
              <KeyTile icon={Landmark} label="다음 FOMC 확률" href="/macro#fedwatch"><FedWatchTile /></KeyTile>
              <KeyTile icon={TrendingUp} label="핵심 매크로 신호" href="/macro"><span className="line-clamp-3">{d.top_macro_signal ?? "—"}</span></KeyTile>
              <KeyTile icon={Target} label="최상위 섹터" href="/recommendations">{d.top_sector ? <><b>{groupKo(d.top_sector.group)}</b> <span className="text-muted-foreground">{kindKo(d.top_sector.kind)} · {d.top_sector.score.toFixed(0)}점</span></> : "—"}</KeyTile>
              <KeyTile icon={Sparkles} label="최고 기술적 셋업" href={d.top_setup ? `/stock/${d.top_setup.symbol}` : "/recommendations"}>
                {d.top_setup ? <><b className="font-mono">{d.top_setup.symbol}</b> <span className="text-muted-foreground">{d.top_setup.score?.toFixed(0)}/100</span><div className="truncate text-xs text-muted-foreground">{d.top_setup.name}</div></> : <span className="text-muted-foreground">{d.scan.state === "running" ? `스캔 중 ${d.scan.processed}/${d.scan.total}…` : "—"}</span>}
              </KeyTile>
              <KeyTile icon={AlertTriangle} label="최대 위험 요인" href="/macro"><span className="line-clamp-3 text-down">{d.top_risk ?? "—"}</span></KeyTile>
              <KeyTile icon={Newspaper} label="주요 뉴스 (24시간)" href="/news"><span className="text-2xl font-semibold tabular">{d.important_news_count}</span> <span className="text-xs text-muted-foreground">영향도 높음 이상</span></KeyTile>
              <KeyTile icon={Bell} label="관심종목 알림" href="/alerts"><span className="text-2xl font-semibold tabular">{d.watchlist_alerts}</span> <span className="text-xs text-muted-foreground">읽지 않음</span></KeyTile>
            </div>
          )}
        </QueryState>
      )}

      <FearGreedPanel />

      <Section title="시장 현황" subtitle="주요 지수 · 실시간/지연 여부 표시 · 클릭하면 상세 분석" right={<UpdatedAt at={snap.data?.updated_at} source="Yahoo Finance" />} bodyClassName="p-3">
        {snap.isError ? <QueryState q={snap} label="시장 현황">{() => null}</QueryState> : <SnapshotCards cards={snap.data?.cards} loading={snap.isLoading} />}
      </Section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <Section title="오늘의 시장 브리핑" subtitle={brief.data ? `${brief.data.method} · ${timeKST(brief.data.generated_at)} 생성` : undefined}>
          <QueryState q={brief} label="브리핑" skeleton={<SkeletonBlock rows={8} />}>
            {(b) => (
              <div className="grid gap-5 md:grid-cols-2">
                <div className="space-y-4">
                  <BriefList title="시장" items={b.market} />
                  <BriefList title="매크로" items={b.macro} />
                  <BriefList title="해석" items={b.interpretation.length ? b.interpretation : ["규칙 기반 해석 조건에 해당하는 변화 없음 (변동폭이 작음)"]} />
                  <BriefList title="강세 섹터 (1일)" items={b.strong_sectors} tone="up" />
                  <BriefList title="약세 섹터 (1일)" items={b.weak_sectors} tone="down" />
                </div>
                <div className="space-y-4">
                  <div>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">주요 뉴스 (24시간)</h3>
                    <ul className="space-y-1.5 text-sm">
                      {b.news.length === 0 && <li className="text-muted-foreground">최근 24시간 내 중요도 높은 뉴스 없음</li>}
                      {b.news.map((n, i) => (
                        <li key={i}>
                          <a href={n.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{n.title}</a>
                          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">{n.source} · {ago(n.published_at)} <ImpactBadge i={n.impact} /><SentimentBadge s={n.sentiment} /></div>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <BriefList title="주요 위험 요인" items={b.risks} tone="down" />
                  <div>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">상위 기술적 셋업</h3>
                    <ul className="space-y-1 text-sm">{b.top_setups.map((s) => (
                      <li key={s.symbol}><Link href={`/stock/${s.symbol}`} className="font-mono font-semibold hover:underline">{s.symbol}</Link> <span className="text-muted-foreground">{s.score?.toFixed(0)} · {s.reason[0]}</span></li>
                    ))}</ul>
                  </div>
                </div>
              </div>
            )}
          </QueryState>
        </Section>
        <Section title="마지막 방문 이후 변화" subtitle="관심종목의 기술적 점수 변화">
          <QueryState q={dash} label="변화">
            {(d) => d.since_last_visit.length === 0 ? (
              <p className="text-sm text-muted-foreground">아직 의미 있는 변화가 없습니다. 일일 스캔이 실행될 때마다 점수 기록이 쌓입니다 (거래일마다 스냅샷 저장).</p>
            ) : (
              <ul className="divide-y text-sm">
                {d.since_last_visit.slice(0, 10).map((c) => (
                  <li key={c.symbol} className="py-2">
                    <div className="flex items-center justify-between">
                      <Link href={`/stock/${c.symbol}`} className="font-mono font-semibold hover:underline">{c.symbol}</Link>
                      <span className="flex items-center gap-1.5 tabular">{c.from.toFixed(0)}<ArrowRight className="size-3" /><span className={c.change > 0 ? "text-up" : "text-down"}>{c.to.toFixed(0)}</span></span>
                    </div>
                    <div className="text-xs text-muted-foreground">{signalKo(c.signal[0])} → {signalKo(c.signal[1])} · {c.from_date} → {c.to_date}{c.drivers.length ? ` · ${c.drivers.join(", ")}` : ""}</div>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Section>
      </div>

      <Section title="상위 투자 기회" subtitle="S&P 500 · 나스닥 100(근사) · 코스피 100 · 코스닥 50 · 관심종목 최신 스캔 기준 순위"
        right={top.data?.scan && <span>스캔 {scanStateKo(top.data.scan.state)}{top.data.scan.finished_at ? ` · ${ago(top.data.scan.finished_at)}` : ""}</span>}>
        <QueryState q={top} label="투자 기회" skeleton={<SkeletonBlock rows={6} />}>
          {(t) => (
            <>
            <ExcludedNote e={t.excluded_overbought} />
            <Tabs defaultValue="combined">
              <TabsList>
                <TabsTrigger value="combined">종합</TabsTrigger>
                <TabsTrigger value="technical">기술적</TabsTrigger>
                <TabsTrigger value="macro">매크로</TabsTrigger>
              </TabsList>
              <TabsContent value="combined" className="mt-2"><OpportunityList items={t.combined} scoreKey="combined" /></TabsContent>
              <TabsContent value="technical" className="mt-2"><OpportunityList items={t.technical} /></TabsContent>
              <TabsContent value="macro" className="mt-2">
                <div className="grid gap-4 md:grid-cols-2">
                  <ul className="divide-y">
                    {t.macro_groups.map((g) => (
                      <li key={g.group} className="py-2 text-sm">
                        <div className="flex justify-between"><b>{groupKo(g.group)} <span className="font-normal text-muted-foreground">({kindKo(g.kind)})</span></b><span className="tabular">{g.score.toFixed(0)}</span></div>
                        <div className="text-xs text-muted-foreground">{g.reasons[0] ?? ""}{g.risks[0] ? <span className="text-down"> · {g.risks[0]}</span> : null}</div>
                      </li>
                    ))}
                  </ul>
                  <ul className="divide-y">
                    {t.macro_stocks.map((s) => (
                      <li key={s.symbol} className="py-2 text-sm">
                        <div className="flex justify-between"><Link href={`/stock/${s.symbol}`} className="font-mono font-semibold hover:underline">{s.symbol}</Link><span className="tabular">{s.macro.toFixed(0)}</span></div>
                        <div className="text-xs text-muted-foreground">{s.name} · {s.group ? groupKo(s.group) : "—"} · 기술 {s.technical.toFixed(0)}</div>
                      </li>
                    ))}
                  </ul>
                </div>
              </TabsContent>
            </Tabs>
            </>
          )}
        </QueryState>
      </Section>
    </div>
  );
}
