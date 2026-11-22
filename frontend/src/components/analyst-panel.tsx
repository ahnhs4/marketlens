"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Pill, QueryState, SkeletonBlock } from "@/components/common";
import { api } from "@/lib/api";
import { isNum, pct } from "@/lib/format";

export interface AnalystReport {
  date: string; firm: string; pt: number | null; pt_prior?: number | null; pt_action?: string; pt_action_ko?: string; action?: string; action_ko?: string;
  grade?: string; grade_from?: string; title?: string; summary?: string; url?: string; opinion_ko?: string;
  headline?: { title: string; source: string | null; url: string | null; published_at: string | null };
}
export interface AnalystView {
  symbol: string; market: "US" | "KR"; available: boolean; message?: string; source?: string; price?: number | null; currency?: string;
  consensus?: { mean: number; median?: number; high?: number; low?: number; analysts?: number | null; n_label: string; basis: string } | null;
  upside_pct?: number | null;
  ratings?: { strongBuy: number; buy: number; hold: number; sell: number; strongSell: number } | null;
  eps?: Record<string, { label: string; current: number; ago90: number; chg_pct: number | null; up30: number | null; down30: number | null }> | null;
  counts?: { pt_raises_90: number; pt_cuts_90: number; upgrades_30: number; downgrades_30: number; reports_90: number };
  recent?: AnalystReport[]; flow?: string;
  micro?: { score: number; points: number; details: string[]; study: string; weight: number } | null;
  updated_at?: string;
}

export const useAnalyst = (symbol: string) =>
  useQuery({ queryKey: ["analyst", symbol], queryFn: () => api<AnalystView>(`/api/stock/${encodeURIComponent(symbol)}/analyst`), staleTime: 30 * 60_000 });

const money = (v: number | null | undefined, ccy?: string) =>
  !isNum(v) ? "—" : ccy === "KRW" ? `${Math.round(v).toLocaleString("ko-KR")}원` : `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function RangeBar({ low, high, mean, price, ccy }: { low: number; high: number; mean: number; price: number; ccy?: string }) {
  const lo = Math.min(low, price), hi = Math.max(high, price);
  const at = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  return (
    <div className="mt-3">
      <div className="relative h-2 rounded-full bg-muted">
        <div className="absolute inset-y-0 rounded-full bg-s1/30" style={{ left: at(low), right: `calc(100% - ${at(high)})` }} />
        <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-s1" style={{ left: at(mean) }} title={`평균 ${money(mean, ccy)}`} />
        <span className="absolute -top-1 h-4 w-0.5 -translate-x-1/2 bg-foreground" style={{ left: at(price) }} title={`현재가 ${money(price, ccy)}`} />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground tabular"><span>최저 {money(low, ccy)}</span><span>▮ 현재가 · ● 평균</span><span>최고 {money(high, ccy)}</span></div>
    </div>
  );
}

function Ratings({ r }: { r: NonNullable<AnalystView["ratings"]> }) {
  const parts = [["강력 매수", r.strongBuy, "bg-up"], ["매수", r.buy, "bg-up/60"], ["보유", r.hold, "bg-muted-foreground/40"], ["매도", r.sell + r.strongSell, "bg-down"]] as const;
  const tot = parts.reduce((s, p) => s + p[1], 0);
  if (!tot) return null;
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded-full">{parts.map(([k, v, c]) => v ? <div key={k} className={c} style={{ width: `${(v / tot) * 100}%` }} /> : null)}</div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground">{parts.map(([k, v]) => v ? <span key={k}>{k} {v}</span> : null)}</div>
    </div>
  );
}

function ReportRow({ r, ccy, market }: { r: AnalystReport; ccy?: string; market: string }) {
  const [open, setOpen] = useState(false);
  const tone = r.pt_action === "Raises" || r.action === "up" ? "up" : r.pt_action === "Lowers" || r.action === "down" ? "down" : "default";
  return (
    <li className="border-b py-2 last:border-0">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="tabular text-muted-foreground">{r.date.slice(2).replaceAll("-", ".")}</span>
        <b>{r.firm}</b>
        {r.action_ko && <Pill tone={r.action === "up" ? "up" : r.action === "down" ? "down" : "default"}>의견 {r.action_ko}</Pill>}
        {(r.opinion_ko || r.grade) && <span>{r.grade_from && r.grade_from !== r.grade ? `${r.grade_from} → ` : ""}{r.opinion_ko || r.grade}</span>}
        <span className={cn("ml-auto tabular", tone === "up" ? "text-up" : tone === "down" ? "text-down" : "")}>
          {isNum(r.pt) ? <>{isNum(r.pt_prior) && r.pt_prior !== r.pt ? `${money(r.pt_prior, ccy)} → ` : ""}{money(r.pt, ccy)}</> : "목표가 없음"}
          {r.pt_action_ko && <span className="ml-1 text-[10px] text-muted-foreground">{r.pt_action_ko}</span>}
        </span>
      </div>
      {market === "KR" ? (
        <div className="mt-1 text-xs">
          <button onClick={() => setOpen(!open)} className="text-left font-medium hover:underline">{r.title}</button>
          {r.summary && <p className={cn("mt-0.5 text-muted-foreground", !open && "line-clamp-2")}>{r.summary}</p>}
          {r.url && <a href={r.url} target="_blank" rel="noreferrer" className="mt-0.5 inline-flex items-center gap-0.5 text-[11px] text-muted-foreground underline">원문 (네이버) <ExternalLink className="size-3" /></a>}
        </div>
      ) : r.headline ? (
        <a href={r.headline.url ?? undefined} target="_blank" rel="noreferrer" className="mt-1 block text-xs hover:underline">
          <span className="text-muted-foreground">사유 기사 · {r.headline.source}: </span>{r.headline.title}
        </a>
      ) : <p className="mt-1 text-[11px] text-muted-foreground">이 의견을 다룬 기사를 찾지 못했습니다 (리포트 원문은 유료)</p>}
    </li>
  );
}

/** Consensus target, flow paragraph and the 5 latest reports. `compact` hides the detail rows behind a toggle. */
export function AnalystPanel({ symbol, compact = false }: { symbol: string; compact?: boolean }) {
  const q = useAnalyst(symbol);
  const [more, setMore] = useState(!compact);
  return (
    <QueryState q={q} label="애널리스트" skeleton={<SkeletonBlock rows={compact ? 3 : 6} />}>
      {(a) => !a.available ? <p className="text-sm text-muted-foreground">{a.message ?? "애널리스트 리포트 · 목표가가 없는 종목입니다 (ETF 등) - Data unavailable"}</p> : (
        <div className="space-y-3 text-sm">
          <div className={cn("grid gap-3", !compact && "md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]")}>
            <div>
              {a.consensus ? (
                <>
                  <div className="text-[11px] text-muted-foreground">평균 목표가 · {a.consensus.n_label}</div>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-2xl font-semibold tabular">{money(a.consensus.mean, a.currency)}</span>
                    {isNum(a.upside_pct) && <span className={cn("font-semibold tabular", a.upside_pct >= 0 ? "text-up" : "text-down")}>현재가 대비 {pct(a.upside_pct, 1)}</span>}
                  </div>
                  <div className="text-[11px] text-muted-foreground tabular">중간값 {money(a.consensus.median, a.currency)} · 현재가 {money(a.price, a.currency)}</div>
                  {isNum(a.consensus.low) && isNum(a.consensus.high) && isNum(a.price) && <RangeBar low={a.consensus.low} high={a.consensus.high} mean={a.consensus.mean} price={a.price} ccy={a.currency} />}
                </>
              ) : <p className="text-xs text-muted-foreground">최근 1년 목표가 데이터가 부족합니다.</p>}
            </div>
            <div className="space-y-2">
              {a.ratings && <><div className="text-[11px] text-muted-foreground">투자의견 분포</div><Ratings r={a.ratings} /></>}
              {a.counts && (
                <div className="flex flex-wrap gap-1.5 text-[11px]">
                  <Pill tone={a.counts.pt_raises_90 > a.counts.pt_cuts_90 ? "up" : a.counts.pt_cuts_90 > a.counts.pt_raises_90 ? "down" : "default"}>90일 목표가 상향 {a.counts.pt_raises_90} · 하향 {a.counts.pt_cuts_90}</Pill>
                  {(a.counts.upgrades_30 > 0 || a.counts.downgrades_30 > 0) && <Pill>30일 의견 상향 {a.counts.upgrades_30} · 하향 {a.counts.downgrades_30}</Pill>}
                </div>
              )}
              {a.eps && (
                <div className="flex flex-wrap gap-1.5 text-[11px]">{Object.values(a.eps).map((e) => isNum(e.chg_pct) && (
                  <Pill key={e.label} tone={e.chg_pct > 0 ? "up" : e.chg_pct < 0 ? "down" : "default"}>{e.label} EPS 추정 90일 {pct(e.chg_pct, 1)}{isNum(e.up30) ? ` (30일 상향 ${e.up30} · 하향 ${e.down30})` : ""}</Pill>))}</div>
              )}
            </div>
          </div>

          {a.flow && (
            <div className="rounded-md border bg-muted/30 p-2.5">
              <div className="mb-1 text-[11px] font-medium text-muted-foreground">사유와 흐름 <span className="font-normal">· 리포트 기록에서 규칙으로 작성 (AI 아님)</span></div>
              <p className="text-sm leading-relaxed">{a.flow}</p>
            </div>
          )}

          {a.micro ? (
            <p className="text-[11px] text-muted-foreground">투자 의견 점수 반영: <b className={a.micro.points > 0 ? "text-up" : a.micro.points < 0 ? "text-down" : ""}>{a.micro.points > 0 ? "+" : ""}{a.micro.points}점</b> ({a.micro.details[0].replace(/ \([+−-]?\d+\)/, "")}) · 판단 점수의 {a.micro.weight * 100}% 비중 · 근거 {a.micro.study}. 목표가 수정 방향 · 의견 변경 · EPS 추정 변화는 과거 데이터상 효과가 확인되지 않아 참고로만 보여 줍니다.</p>
          ) : <p className="text-[11px] text-muted-foreground">목표가를 낸 증권사가 3곳 미만이라 투자 의견 점수에는 반영하지 않습니다.</p>}

          {a.recent && a.recent.length > 0 && (
            <div>
              <button onClick={() => setMore(!more)} className="text-xs font-medium text-muted-foreground hover:text-foreground">최근 리포트 · 의견 {a.recent.length}건 {more ? "▴" : "▾"}</button>
              {more && <ul className="mt-1">{a.recent.map((r, i) => <ReportRow key={i} r={r} ccy={a.currency} market={a.market} />)}</ul>}
            </div>
          )}
          <p className="text-[10px] text-muted-foreground">출처 {a.source}{a.market === "US" ? " (등급 · 목표가 변경) · 사유는 해당 의견을 다룬 기사 제목 (Google News)" : " (증권사 리포트 요약)"}</p>
        </div>
      )}
    </QueryState>
  );
}
