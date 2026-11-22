"use client";

import { cn } from "cn";
import { Clock, Zap } from "lucide-react";
import Link from "next/link";

import { Delta, Pill, ScoreMeter, SignalBadge, SkeletonBlock, Sparkline, scoreTone } from "@/components/common";
import { isNum, price, timeKST } from "@/lib/format";
import { groupKo, marketStateKo, ratingKo, regimeKo, trendKo } from "@/lib/i18n";
import type { Regime, ScanRow, SnapshotCard } from "@/lib/types";


export function SnapshotCards({ cards, loading }: { cards?: SnapshotCard[]; loading?: boolean }) {
  if (loading || !cards) {
    return <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{Array.from({ length: 10 }).map((_, i) => <div key={i} className="h-28 rounded-xl border bg-card p-3.5 shadow-card"><SkeletonBlock rows={3} /></div>)}</div>;
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {cards.map((c) => {
        const vix = c.symbol === "^VIX";
        return (
          <Link key={c.symbol} href={`/stock/${encodeURIComponent(c.symbol)}`} className="group rounded-xl border bg-card p-3.5 shadow-card transition-colors hover:border-gold/35">
            <div className="flex items-start justify-between gap-1">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium">{c.name}</div>
                <div className="text-[10px] text-muted-foreground">{c.region} · {marketStateKo(c.status)}</div>
              </div>
              {isNum(c.delay_minutes) && (
                <span className={cn("inline-flex items-center gap-0.5 text-[10px]", c.delay_minutes === 0 ? "text-up" : "text-warn")} title={c.delay_minutes === 0 ? "실시간" : `${c.delay_minutes}분 지연`}>
                  {c.delay_minutes === 0 ? <Zap className="size-3" /> : <Clock className="size-3" />}{c.delay_minutes === 0 ? "실시간" : `${c.delay_minutes}분 지연`}
                </span>
              )}
            </div>
            {c.price === null || c.price === undefined ? <p className="mt-3 text-xs text-muted-foreground">데이터를 불러올 수 없음</p> : (
              <>
                <div className="mt-1.5 flex items-baseline justify-between gap-1">
                  <span className="text-base font-semibold tabular">{price(c.price)}</span>
                  {/* VIX up = risk up: color by meaning, but keep the sign visible */}
                  <Delta value={c.change_pct} className={cn("text-xs", vix && isNum(c.change_pct) && (c.change_pct > 0 ? "!text-down" : "!text-up"))} />
                </div>
                <div className="mt-0.5 flex justify-between text-[10px] text-muted-foreground tabular">
                  <span>저 {price(c.low)}</span><span>고 {price(c.high)}</span>
                </div>
                <Sparkline data={c.sparkline} className="mt-1 h-7" positive={vix ? (c.sparkline.at(-1) ?? 0) < (c.sparkline[0] ?? 0) : undefined} />
                <div className="flex items-center justify-between text-[10px]">
                  <span className={cn(c.trend.label === "Uptrend" ? "text-up" : c.trend.label === "Downtrend" ? "text-down" : "text-muted-foreground")}>{trendKo(c.trend.label)}</span>
                  <span className="text-muted-foreground">{c.quote_time ? timeKST(c.quote_time).replace(" (한국)", "") : ""}</span>
                </div>
              </>
            )}
          </Link>
        );
      })}
    </div>
  );
}

export function RegimeBadge({ r, size = "md" }: { r: Pick<Regime, "label" | "score">; size?: "md" | "lg" }) {
  const tone = r.label === "Risk-On" ? "text-up border-up/30 bg-up/10" : r.label === "Risk-Off" ? "text-down border-down/30 bg-down/10" : "text-foreground border-border bg-muted";
  return (
    <span className={cn("inline-flex items-center gap-2 rounded-md border font-semibold", tone, size === "lg" ? "px-3 py-1.5 text-lg" : "px-2 py-0.5 text-sm")}>
      {regimeKo(r.label)} <span className="tabular">{r.score.toFixed(0)}/100</span>
    </span>
  );
}

export function OpportunityList({ items, scoreKey = "score", empty }: { items: ScanRow[]; scoreKey?: "score" | "combined" | "macro"; empty?: string }) {
  if (!items?.length) return <p className="text-sm text-muted-foreground">{empty ?? "아직 결과가 없습니다 - 종목 스캔이 진행 중일 수 있습니다."}</p>;
  return (
    <ul className="divide-y">
      {items.map((r) => {
        const s = (r[scoreKey] ?? r.technical) as number;
        return (
          <li key={r.symbol} className="py-2">
            <div className="flex items-center justify-between gap-2">
              <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="min-w-0 hover:underline">
                <span className="font-mono text-sm font-semibold">{r.symbol}</span>
                <span className="ml-2 truncate text-xs text-muted-foreground">{r.name_ko ?? r.name}</span>
              </Link>
              <span className="flex shrink-0 items-center gap-2">
                <span className={cn("text-sm font-semibold tabular", scoreTone(s))}>{isNum(s) ? s.toFixed(0) : "—"}</span>
                {r.rating ? <Pill tone={r.rating.includes("Buy") ? "up" : r.rating.includes("Sell") ? "down" : "default"}>{ratingKo(r.rating)}</Pill> : <SignalBadge signal={r.signal} size="sm" />}
              </span>
            </div>
            <ScoreMeter score={s} className="mt-1" />
            <div className="mt-1 text-xs text-muted-foreground">
              {r.reason?.[0] && <span className="text-foreground/80">{r.reason[0]}</span>}
              {r.risks?.[0] && <span> · <span className="text-down">리스크:</span> {r.risks[0]}</span>}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-1 text-[10px] text-muted-foreground">
              <span>{r.time_horizon}</span>{r.group && <span>· {groupKo(r.group)}</span>}<span>· 위험도 {r.risk === "High" ? "높음" : r.risk === "Medium" ? "보통" : "낮음"}</span>
              {isNum(r.macro) && scoreKey !== "macro" && <span>· 매크로 {r.macro.toFixed(0)}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
