"use client";

import { cn } from "cn";
import { ArrowDown, ArrowUp, Bot, ChevronRight, ExternalLink, Layers, ListChecks, MoveHorizontal } from "lucide-react";
import { useState } from "react";

import { ImpactBadge, Pill, SentimentBadge } from "@/components/common";
import { ago, timeET, timeKST } from "@/lib/format";
import { categoryKo } from "@/lib/i18n";
import type { ChainNode, NewsItem, NewsSummary } from "@/lib/types";

const NODE_TYPE: Record<string, string> = { macro: "매크로", asset: "자산", sector: "섹터", stock: "종목" };

export function ImpactChain({ chain, compact = false }: { chain: ChainNode[]; compact?: boolean }) {
  if (!chain?.length) return null;
  return (
    <ol className={cn("flex flex-wrap items-center gap-1", compact ? "text-[10px]" : "text-xs")} aria-label="뉴스 → 시장 영향 경로">
      {chain.map((n, i) => {
        const Icon = n.direction === "up" ? ArrowUp : n.direction === "down" ? ArrowDown : MoveHorizontal;
        return (
          <li key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="size-3 text-muted-foreground" aria-hidden />}
            <span className={cn("inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5",
              n.type === "macro" ? "border-s7/40" : n.type === "sector" ? "border-s1/40" : n.type === "stock" ? "border-s2/40" : "border-border")}>
              <span className="text-[9px] text-muted-foreground">{NODE_TYPE[n.type] ?? n.type}</span>
              <span className="font-medium">{n.node}</span>
              <Icon className={cn("size-3", n.direction === "up" ? "text-up" : n.direction === "down" ? "text-down" : "text-muted-foreground")} aria-label={n.direction === "up" ? "상승" : n.direction === "down" ? "하락" : "혼조"} />
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function NewsRow({ n, showChain = true }: { n: NewsItem; showChain?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="py-2.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <a href={n.url ?? undefined} target="_blank" rel="noopener noreferrer" className="group text-sm font-medium leading-snug hover:underline">
            {n.title}<ExternalLink className="ml-1 inline size-3 opacity-0 group-hover:opacity-60" aria-hidden />
          </a>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
            <span className="font-medium text-foreground/80">{n.source ?? n.provider}</span>
            <span title={`${timeKST(n.published_at)} · ${timeET(n.published_at)}`}>· {timeKST(n.published_at)} ({ago(n.published_at)})</span>
            {n.cluster_size > 1 && <span className="inline-flex items-center gap-0.5"><Layers className="size-3" />{n.cluster_size}개 매체</span>}
            <ImpactBadge i={n.impact} />
            <SentimentBadge s={n.sentiment} />
            <Pill>{categoryKo(n.category)}</Pill>
            {n.tickers.slice(0, 4).map((t) => <a key={t} href={`/stock/${t}`} className="rounded bg-muted px-1 font-mono text-[10px] hover:bg-accent">{t}</a>)}
          </div>
          {showChain && n.chain.length > 0 && <div className="mt-1.5"><ImpactChain chain={n.chain} compact /></div>}
          {open && (
            <div className="mt-2 space-y-1 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
              <p><span className="font-medium text-foreground">{n.method === "ai" ? `AI 분석 (${n.model})` : "규칙 기반 분류"}:</span> {n.rationale}</p>
              <p>중요도 {n.importance}/100 · 신뢰도 {(n.confidence * 100).toFixed(0)}% · 우선순위 {n.priority}</p>
              {n.also.length > 0 && <p>같은 내용을 보도한 매체: {n.also.map((a) => a.source).filter(Boolean).join(", ")}</p>}
            </div>
          )}
        </div>
        <button onClick={() => setOpen((o) => !o)} className="shrink-0 rounded px-1 text-[11px] text-primary hover:bg-primary/10" aria-expanded={open}>
          {open ? "접기" : "근거"}
        </button>
      </div>
    </li>
  );
}

export function SummaryBlock({ s, title = "시장 해석" }: { s: NewsSummary; title?: string }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">{s.method === "ai" ? <Bot className="size-4" /> : <ListChecks className="size-4" />}{title}</h3>
        <span className="text-[11px] text-muted-foreground">
          {s.method === "ai" ? `AI · ${s.model}` : "규칙 기반 집계 (AI 키 미설정)"} · {timeKST(s.generated_at)} 생성 · 기사 {s.input_count}건
          {s.data_from && ` · ${timeKST(s.data_from)} → ${timeKST(s.data_to)}`}
        </span>
      </div>
      {s.headline && <p className="text-sm text-muted-foreground">{s.headline}</p>}
      <ul className="space-y-1.5 text-sm">
        {s.bullets.map((b, i) => <li key={i} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-foreground/60" aria-hidden />{b}</li>)}
      </ul>
      {(s.risks.length > 0 || s.opportunities.length > 0) && (
        <div className="grid gap-3 text-xs sm:grid-cols-2">
          {s.opportunities.length > 0 && <div><div className="mb-1 font-semibold text-up">긍정 요인</div><ul className="space-y-1 text-muted-foreground">{s.opportunities.map((x, i) => <li key={i}>· {x}</li>)}</ul></div>}
          {s.risks.length > 0 && <div><div className="mb-1 font-semibold text-down">리스크</div><ul className="space-y-1 text-muted-foreground">{s.risks.map((x, i) => <li key={i}>· {x}</li>)}</ul></div>}
        </div>
      )}
    </div>
  );
}
