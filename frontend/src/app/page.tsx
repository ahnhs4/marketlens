"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Activity, AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight, Bell, ChevronDown, Grid3x3, Landmark, LayoutGrid, Newspaper, Sparkles, Target, TrendingUp } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Delta, ImpactBadge, QueryState, Section, SentimentBadge, SkeletonBlock, UpdatedAt } from "@/components/common";
import { ExcludedNote } from "@/components/excluded-note";
import { FearGreedPanel } from "@/components/fear-greed";
import { FedWatchTile } from "@/components/fedwatch";
import { RecapCard } from "@/components/recap";
import { OpportunityList, RegimeBadge, SnapshotCards } from "@/components/market";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { ago, price, timeKST } from "@/lib/format";
import { useScope, useSnapshot } from "@/lib/hooks";
import { groupKo, kindKo, overlayKo, quadrantKo, scanStateKo, signalKo } from "@/lib/i18n";
import { STATIC } from "@/lib/static";
import { useStored } from "@/lib/stored";
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

// ---- At a glance: verdict → watchlist signals → buy candidates → cautions. Everything else is folded below. ----

interface Pick5 { symbol: string; name: string | null; market: string; price: number | null; change_pct: number | null; technical: number | null; signal: string | null }
interface Summary {
  headline: {
    tone: "good" | "warn" | "bad" | "neutral"; text: string;
    parts: { label: string; value: string; detail: string | null; href: string }[];
    drivers: { text: string; direction: string }[];
  };
  signals: (Pick5 & { tags: { text: string; tone: "up" | "info" }[] })[]; signals_total: number;
  candidates: (Pick5 & { reason: string[]; time_horizon: string | null })[]; candidates_note: string | null;
  cautions: (Pick5 & { reasons: string[] })[]; cautions_total: number;
  watch_count: number; covered: number; missing: string[]; updated_at: string; note: string | null;
}

const TONE = {
  good: { label: "우호적", card: "border-up/40 from-up/[0.09]", pill: "bg-up/15 text-up" },
  warn: { label: "선별 접근", card: "border-warn/45 from-warn/[0.09]", pill: "bg-warn/15 text-warn" },
  bad: { label: "방어 우선", card: "border-down/40 from-down/[0.09]", pill: "bg-down/15 text-down" },
  neutral: { label: "중립", card: "border-border from-foreground/[0.03]", pill: "bg-foreground/10 text-muted-foreground" },
} as const;
const ROWS = 5; // rows per column: keeps the whole board on one laptop screen

function Headline({ h }: { h: Summary["headline"] }) {
  const t = TONE[h.tone] ?? TONE.neutral;
  return (
    <div className={cn("grid gap-4 rounded-2xl border bg-gradient-to-br to-transparent bg-card p-4 shadow-card lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center", t.card)}>
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-[11px] font-medium tracking-[0.16em] text-muted-foreground">지금 시장 <span className={cn("rounded-full px-2 py-0.5 text-[11px] tracking-normal", t.pill)}>{t.label}</span></div>
        <p className="mt-1.5 text-[19px] font-semibold leading-snug tracking-[-0.01em]">{h.text}</p>
        {h.drivers.length > 0 && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {h.drivers.slice(0, 3).map((d, i) => (
              <li key={i} className="flex min-w-0 items-center gap-1">
                {d.direction === "supportive" ? <ArrowUpRight className="size-3.5 shrink-0 text-up" aria-label="우호" /> : <ArrowDownRight className="size-3.5 shrink-0 text-down" aria-label="부담" />}
                <span className="truncate" title={d.text}>{d.text.split(" (")[0]}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {h.parts.map((p) => (
          <Link key={p.label} href={p.href} className="min-w-0 rounded-xl border bg-background/40 px-3 py-2 transition-colors hover:border-gold/40 lg:w-[150px]">
            <div className="truncate text-[10.5px] text-muted-foreground">{p.label}</div>
            <div className="truncate text-[13.5px] font-semibold">{p.value}</div>
            <div className="truncate text-[10.5px] text-muted-foreground">{p.detail ?? " "}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function Board({ title, count, more, children, empty }: { title: string; count?: number; more?: { href: string; label: string }; children: ReactNode[]; empty: string }) {
  return (
    <section className="flex min-w-0 flex-col rounded-2xl border bg-card shadow-card">
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 className="flex items-center gap-2 text-[13.5px] font-semibold tracking-tight">
          <span className="h-3.5 w-[3px] shrink-0 rounded-full bg-gradient-to-b from-gold to-gold/30" aria-hidden />{title}
          {count !== undefined && <span className="rounded-full bg-foreground/[0.07] px-1.5 text-[11px] font-medium tabular text-muted-foreground">{count}</span>}
        </h2>
        {more && <Link href={more.href} className="flex items-center gap-0.5 text-[11.5px] text-muted-foreground hover:text-foreground">{more.label}<ArrowRight className="size-3" /></Link>}
      </header>
      {children.length === 0 ? <p className="px-4 py-6 text-[12.5px] leading-relaxed text-muted-foreground">{empty}</p> : <ul className="divide-y divide-border/60">{children}</ul>}
    </section>
  );
}

function StockRow({ r, children }: { r: Pick5; children: ReactNode }) {
  return (
    <li>
      <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="flex items-center justify-between gap-3 px-4 py-2 transition-colors hover:bg-accent/40">
        <div className="min-w-0">
          <div className="flex items-baseline gap-1.5"><span className="truncate text-[13px] font-medium">{r.name ?? r.symbol}</span><span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">{r.symbol}</span></div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{children}</div>
        </div>
        <div className="shrink-0 text-right tabular">
          <div className="text-[12.5px]">{price(r.price, r.market === "KR" ? "KRW" : "USD")}</div>
          <Delta value={r.change_pct} showIcon={false} className="text-[11px]" />
        </div>
      </Link>
    </li>
  );
}

function Glance({ d }: { d: Summary }) {
  const mine = STATIC ? "데모 종목" : "관심종목";
  return (
    <>
      <Headline h={d.headline} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Board title={`${mine} 중 신호 뜬 것`} count={d.signals_total} more={{ href: "/matrix", label: "매트릭스" }}
          empty={d.note ?? (d.watch_count === 0 ? "관심종목이 비어 있습니다 - 관심종목에 종목을 추가하면 신호가 여기에 표시됩니다." : `${mine} ${d.covered}개 중 오늘 새로 뜬 매수 신호가 없습니다.`)}>
          {d.signals.slice(0, ROWS).map((s) => (
            <StockRow key={s.symbol} r={s}>
              {s.tags.map((t, i) => <span key={i} className={cn(i > 0 && "ml-1.5", t.tone === "up" ? "text-up" : "text-primary")}>{t.text}</span>)}
            </StockRow>
          ))}
        </Board>
        <Board title="매수 후보 5" more={{ href: "/recommendations", label: "추천 종목" }} empty="스캔 결과가 아직 없습니다 (Data unavailable)">
          {d.candidates.slice(0, ROWS).map((c) => (
            <StockRow key={c.symbol} r={c}>
              <span className="mr-1.5 rounded bg-up/12 px-1 font-medium tabular text-up">기술 {c.technical?.toFixed(0) ?? "—"}</span>{c.reason[0] ?? signalKo(c.signal ?? "")}
            </StockRow>
          ))}
        </Board>
        <Board title="주의 종목" count={d.cautions_total} more={{ href: "/matrix", label: "매트릭스" }} empty={`${mine} 중 과열 · 매도 신호 · 데드크로스에 해당하는 종목이 없습니다.`}>
          {d.cautions.slice(0, ROWS).map((c) => (
            <StockRow key={c.symbol} r={c}><span className="text-down">{c.reasons.join(" · ")}</span></StockRow>
          ))}
        </Board>
      </div>
      {(d.candidates_note || d.missing.length > 0) && (
        <p className="-mt-2 px-1 text-[11px] text-muted-foreground">
          매수 후보: {d.candidates_note}{d.missing.length > 0 && ` · 스캔 결과가 없는 종목 ${d.missing.join(", ")} (Data unavailable)`} · 투자 권유가 아닌 규칙 기반 참고 자료입니다.
        </p>
      )}
    </>
  );
}

// ---- Folded detail ----

function KeyTiles() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/dashboard"), refetchInterval: 120_000 });
  return (
    <QueryState q={dash} label="대시보드" skeleton={<div className="grid grid-cols-2 gap-3 md:grid-cols-4 2xl:grid-cols-8">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-24 rounded-xl border bg-card p-3.5 shadow-card"><SkeletonBlock rows={2} /></div>)}</div>}>
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
  );
}

function Indices() {
  const snap = useSnapshot();
  return (
    <Section title="시장 현황" subtitle="주요 지수 · 실시간/지연 여부 표시 · 클릭하면 상세 분석" right={<UpdatedAt at={snap.data?.updated_at} source="Yahoo Finance" />} bodyClassName="p-3">
      {snap.isError ? <QueryState q={snap} label="시장 현황">{() => null}</QueryState> : <SnapshotCards cards={snap.data?.cards} loading={snap.isLoading} />}
    </Section>
  );
}

function BriefingSection() {
  const brief = useQuery({ queryKey: ["briefing"], queryFn: () => api<Briefing>("/api/briefing"), staleTime: 300_000 });
  return (
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
  );
}

function Changes() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/api/dashboard"), refetchInterval: 120_000 });
  return (
    <Section title="마지막 방문 이후 변화" subtitle="관심종목의 기술적 점수 변화">
      <QueryState q={dash} label="변화">
        {(d) => d.since_last_visit.length === 0 ? (
          <p className="text-sm text-muted-foreground">아직 의미 있는 변화가 없습니다. 일일 스캔이 실행될 때마다 점수 기록이 쌓입니다 (거래일마다 스냅샷 저장).</p>
        ) : (
          <ul className="grid gap-x-8 text-sm md:grid-cols-2">
            {d.since_last_visit.slice(0, 10).map((c) => (
              <li key={c.symbol} className="border-b border-border/60 py-2">
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
  );
}

function Opportunities() {
  const top = useQuery({ queryKey: ["reco", "top"], queryFn: () => api<Top>("/api/recommendations/top"), staleTime: 300_000 });
  return (
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
  );
}

// Detail sections stay folded (and unmounted, so they cost nothing) until opened; the choice is remembered per browser.
const FOLDS: { key: string; label: string; hint: string; node: () => ReactNode }[] = [
  { key: "recap", label: "지난 12시간 요약", hint: "무엇이 왜 움직였나", node: () => <RecapCard /> },
  { key: "tiles", label: "핵심 지표", hint: "국면 · FOMC · 섹터 · 위험", node: () => <KeyTiles /> },
  { key: "indices", label: "시장 현황", hint: "주요 지수 · 환율 · 원자재", node: () => <Indices /> },
  { key: "feargreed", label: "공포 · 탐욕 지수", hint: "7개 지표 상세", node: () => <FearGreedPanel /> },
  { key: "briefing", label: "오늘의 브리핑", hint: "시장 · 매크로 · 뉴스", node: () => <BriefingSection /> },
  { key: "changes", label: "방문 이후 변화", hint: "관심종목 점수 변화", node: () => <Changes /> },
  { key: "opps", label: "상위 투자 기회", hint: "종합 · 기술적 · 매크로 순위", node: () => <Opportunities /> },
];
const NO_FOLDS = { open: [] as string[] };

export default function Home() {
  const scope = useScope();
  const sum = useQuery({ queryKey: ["overview-summary", scope.qs], queryFn: () => api<Summary>(`/api/overview/summary${scope.qs}`), enabled: scope.ready, refetchInterval: 120_000 });
  const [folds, setFolds] = useStored("ml:dash-folds", NO_FOLDS);
  const toggle = (k: string) => setFolds({ open: folds.open.includes(k) ? folds.open.filter((x) => x !== k) : [...folds.open, k] });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-[22px] font-semibold leading-tight tracking-[-0.025em]">마켓 대시보드</h1>
          <span className="text-[11px] font-medium tracking-[0.16em] text-gold">
            {new Date().toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long", timeZone: "Asia/Seoul" })}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <UpdatedAt at={sum.data?.updated_at} />
          <Link href="/heatmap" className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 hover:border-gold/40 hover:text-foreground"><LayoutGrid className="size-3.5" />히트맵</Link>
          <Link href="/matrix" className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 hover:border-gold/40 hover:text-foreground"><Grid3x3 className="size-3.5" />신호 매트릭스</Link>
        </div>
      </div>

      <QueryState q={{ ...sum, isLoading: sum.isLoading || !scope.ready }} label="시장 요약"
        skeleton={<div className="space-y-4"><div className="h-[118px] rounded-2xl border bg-card p-4 shadow-card"><SkeletonBlock rows={3} /></div><div className="grid gap-4 lg:grid-cols-3">{[0, 1, 2].map((i) => <div key={i} className="h-[300px] rounded-2xl border bg-card p-4 shadow-card"><SkeletonBlock rows={7} /></div>)}</div></div>}>
        {(d) => <Glance d={d} />}
      </QueryState>

      <div>
        <div className="mb-2 px-1 text-[11px] font-medium tracking-[0.16em] text-muted-foreground">상세 내용 · 눌러서 펼치기</div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
          {FOLDS.map((f) => {
            const open = folds.open.includes(f.key);
            return (
              <button key={f.key} onClick={() => toggle(f.key)} aria-expanded={open}
                className={cn("flex items-center justify-between gap-2 rounded-xl border bg-card px-3 py-2 text-left transition-colors hover:border-gold/40", open && "border-gold/50 bg-gold/[0.06]")}>
                <span className="min-w-0"><span className="block truncate text-[12.5px] font-medium">{f.label}</span><span className="block truncate text-[10.5px] text-muted-foreground">{f.hint}</span></span>
                <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180 text-gold")} aria-hidden />
              </button>
            );
          })}
        </div>
      </div>
      {FOLDS.filter((f) => folds.open.includes(f.key)).map((f) => <div key={f.key}>{f.node()}</div>)}
    </div>
  );
}
