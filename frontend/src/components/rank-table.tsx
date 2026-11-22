"use client";

import { cn } from "cn";
import { ArrowDownUp } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Pill, SignalBadge, scoreTone } from "@/components/common";
import { compact, frac, isNum, num, pct } from "@/lib/format";
import { levelKo, patternStatusKo, ratingKo, structureKo } from "@/lib/i18n";
import type { ScanRow } from "@/lib/types";

export type Col = { key: string; label: string; render?: (r: ScanRow) => React.ReactNode; sort?: (r: ScanRow) => number | null; className?: string };

export const COLS: Record<string, Col> = {
  symbol: { key: "symbol", label: "종목", render: (r) => (
    <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="block min-w-0 hover:underline">
      <span className="font-mono font-semibold">{r.symbol}</span>
      <span className="block max-w-44 truncate text-[11px] text-muted-foreground">{r.name_ko ?? r.name}</span>
    </Link>) },
  score: { key: "technical", label: "기술", sort: (r) => r.technical, render: (r) => <b className={cn("tabular", scoreTone(r.technical))}>{r.technical.toFixed(0)}</b> },
  macro: { key: "macro", label: "매크로", sort: (r) => r.macro ?? null, render: (r) => <span className={cn("tabular", scoreTone(r.macro))}>{isNum(r.macro) ? r.macro.toFixed(0) : "—"}</span> },
  combined: { key: "combined", label: "종합", sort: (r) => r.combined ?? null, render: (r) => <b className={cn("tabular", scoreTone(r.combined))}>{isNum(r.combined) ? r.combined.toFixed(1) : "—"}</b> },
  rating: { key: "rating", label: "등급", render: (r) => r.rating ? <Pill tone={r.rating.includes("Buy") ? "up" : r.rating.includes("Sell") ? "down" : "default"}>{ratingKo(r.rating)}</Pill> : null },
  signal: { key: "signal", label: "신호", render: (r) => <SignalBadge signal={r.signal} size="sm" /> },
  change: { key: "change_pct", label: "당일", sort: (r) => r.change_pct, render: (r) => <span className={cn("tabular", isNum(r.change_pct) && r.change_pct >= 0 ? "text-up" : "text-down")}>{pct(r.change_pct)}</span> },
  trend: { key: "trend", label: "이평 배열", render: (r) => <span className="text-xs">{r.trend ? structureKo(r.trend) : "—"}</span> },
  mom: { key: "mom20", label: "20일 수익률", sort: (r) => r.mom20, render: (r) => <span className="tabular">{frac(r.mom20)}</span> },
  rsi: { key: "rsi", label: "RSI", sort: (r) => r.rsi, render: (r) => <span className="tabular">{num(r.rsi, 0)}</span> },
  vol: { key: "vol_ratio", label: "거래량 배율", sort: (r) => r.vol_ratio, render: (r) => <span className="tabular">{isNum(r.vol_ratio) ? `${r.vol_ratio.toFixed(1)}x` : "—"}</span> },
  pattern: { key: "pattern", label: "패턴", render: (r) => <span className="text-xs text-muted-foreground">{patternStatusKo(r.pattern)}</span> },
  risk: { key: "risk", label: "위험도", render: (r) => <Pill tone={r.risk === "High" ? "down" : r.risk === "Medium" ? "warn" : "default"}>{levelKo(r.risk)}</Pill> },
  mcap: { key: "market_cap", label: "시가총액", sort: (r) => r.market_cap ?? null, render: (r) => <span className="tabular">{compact(r.market_cap, "USD")}</span> },
  pe: { key: "pe", label: "PER", sort: (r) => r.pe ?? null, render: (r) => <span className="tabular">{num(r.pe, 1)}</span> },
  pbr: { key: "pbr", label: "PBR", sort: (r) => (isNum(r.pbr) ? -r.pbr : null), render: (r) => <span className="tabular">{num(r.pbr, 2)}</span> },
  roe: { key: "roe", label: "ROE", sort: (r) => r.roe ?? null, render: (r) => <span className="tabular">{isNum(r.roe) ? `${r.roe.toFixed(1)}%` : "—"}</span> },
  fin: { key: "fin_score", label: "재무", sort: (r) => r.fin_score ?? null, render: (r) => <span className={cn("tabular", scoreTone(r.fin_score))}>{isNum(r.fin_score) ? r.fin_score.toFixed(0) : "—"}</span> },
  reason: { key: "reason", label: "근거", render: (r) => (
    <div className="max-w-80 text-xs">
      <span>{r.reason?.slice(0, 2).join(" · ")}</span>
      {r.risks?.[0] && <span className="block text-down">위험: {r.risks[0]}</span>}
      {r.breakdown && <span className="block text-muted-foreground">= {Math.round(r.breakdown.w_tech * 100)}% × {r.breakdown.technical.toFixed(0)} + {Math.round(r.breakdown.w_macro * 100)}% × {r.breakdown.macro.toFixed(0)}</span>}
    </div>) },
  horizon: { key: "time_horizon", label: "투자 기간", render: (r) => <span className="text-[11px] text-muted-foreground">{r.time_horizon}</span> },
};

export function RankTable({ rows, cols, initialSort }: { rows: ScanRow[]; cols: Col[]; initialSort?: string }) {
  const [sortKey, setSortKey] = useState(initialSort ?? cols.find((c) => c.sort)?.key ?? "");
  const [desc, setDesc] = useState(true);
  const sorted = useMemo(() => {
    const col = cols.find((c) => c.key === sortKey);
    if (!col?.sort) return rows;
    return [...rows].sort((a, b) => {
      const x = col.sort!(a), y = col.sort!(b);
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return desc ? y - x : x - y;
    });
  }, [rows, cols, sortKey, desc]);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="w-8 py-2 font-normal">#</th>
            {cols.map((c) => (
              <th key={c.key} className="py-2 pr-3 font-normal">
                {c.sort ? (
                  <button className={cn("inline-flex items-center gap-1 hover:text-foreground", sortKey === c.key && "text-foreground")}
                    onClick={() => { if (sortKey === c.key) setDesc(!desc); else { setSortKey(c.key); setDesc(true); } }}>
                    {c.label}<ArrowDownUp className="size-3" />
                  </button>
                ) : c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={r.symbol} className="border-b align-top last:border-0 hover:bg-accent/40">
              <td className="py-2 text-xs text-muted-foreground tabular">{i + 1}</td>
              {cols.map((c) => <td key={c.key} className={cn("py-2 pr-3", c.className)}>{c.render ? c.render(r) : String((r as unknown as Record<string, unknown>)[c.key] ?? "—")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
