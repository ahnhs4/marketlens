"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Delta, PageHeader, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { isNum, num, price } from "@/lib/format";
import { useScope } from "@/lib/hooks";
import { STATIC } from "@/lib/static";

// Signal matrix: one row per watchlist stock, one column per indicator, coloured so overheated (red) and
// oversold (blue) names stand out at a glance.

interface Row {
  symbol: string; name: string | null; name_ko: string | null; market: string; price: number | null; change_pct: number | null; technical: number | null; signal: string | null;
  trend: number | null; trend_label: string | null; rsi: number | null; stoch_short: number | null; stoch_medium: number | null; stoch_long: number | null;
  vol_ratio: number | null; fscore: number | null; dist_high: number | null; state: "overheated" | "oversold" | null; oversold_reasons: string[]; as_of: string | null;
}
interface Matrix { rows: Row[]; missing: string[]; columns: { key: string; label: string; help: string }[]; legend: string; updated_at: string; note: string | null }
type Key = "trend" | "rsi" | "stoch_short" | "stoch_medium" | "stoch_long" | "vol_ratio" | "fscore" | "dist_high";

const HOT = "214, 82, 72", COLD = "58, 114, 214", GOLD = "196, 160, 82";
const tint = (rgb: string, a: number) => ({ backgroundColor: `rgba(${rgb}, ${Math.min(0.62, Math.max(0, a)).toFixed(2)})` });
/** Oscillator heat: above `hi` runs hot, below `lo` runs cold, the 10 points before each threshold get a faint wash. */
function osc(v: number | null, lo: number, hi: number) {
  if (!isNum(v)) return undefined;
  if (v >= hi) return tint(HOT, 0.3 + ((v - hi) / (100 - hi)) * 0.32);
  if (v <= lo) return tint(COLD, 0.3 + ((lo - v) / lo) * 0.32);
  if (v >= hi - 10) return tint(HOT, 0.1);
  if (v <= lo + 10) return tint(COLD, 0.1);
  return undefined;
}
function cellStyle(k: Key, r: Row) {
  const v = r[k];
  if (!isNum(v)) return undefined;
  if (k === "rsi") return osc(v, 30, 70);
  if (k === "stoch_short" || k === "stoch_medium" || k === "stoch_long") return osc(v, 20, 80);
  if (k === "vol_ratio") return v >= 1.5 ? tint(GOLD, 0.14 + Math.min(1, (v - 1.5) / 2) * 0.3) : undefined;
  if (k === "dist_high") return v >= -0.03 ? tint(HOT, 0.42) : v >= -0.08 ? tint(HOT, 0.12) : v <= -0.2 ? tint(COLD, 0.14 + Math.min(1, (-v - 0.2) / 0.3) * 0.34) : undefined;
  return undefined;
}
function cellText(k: Key, r: Row) {
  const v = r[k];
  if (!isNum(v)) return "—";
  if (k === "trend") return r.trend_label ?? String(v);
  if (k === "vol_ratio") return `${v.toFixed(1)}배`;
  if (k === "dist_high") return v >= -0.0005 ? "고점" : `${(v * 100).toFixed(1)}%`;
  return num(v, 0);
}
const TREND_CLS = ["text-down font-semibold", "text-down", "text-muted-foreground", "text-up", "text-up font-semibold"];

export default function MatrixPage() {
  const scope = useScope();
  const q = useQuery({ queryKey: ["matrix", scope.qs], queryFn: () => api<Matrix>(`/api/overview/matrix${scope.qs}`), enabled: scope.ready, staleTime: 60_000 });
  const [filter, setFilter] = useState<"all" | "overheated" | "oversold">("all");
  const [sort, setSort] = useState<{ key: Key | "name" | "change_pct"; dir: 1 | -1 } | null>(null);
  const rows = useMemo(() => {
    let out = q.data?.rows ?? [];
    if (filter !== "all") out = out.filter((r) => r.state === filter);
    if (sort) {
      const val = (r: Row) => (sort.key === "name" ? (r.name_ko ?? r.name ?? r.symbol) : r[sort.key]);
      out = [...out].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (typeof x === "string" ? x.localeCompare(String(y), "ko") : (x as number) - (y as number)) * sort.dir;
      });
    }
    return out;
  }, [q.data, filter, sort]);
  const head = (key: Key | "name" | "change_pct", label: string, help?: string, right = true) => (
    <th key={key} title={help} className={cn("px-2 py-2 font-normal whitespace-nowrap", right && "text-right")}>
      <button onClick={() => setSort((s) => (s?.key === key ? (s.dir === -1 ? { key, dir: 1 } : null) : { key, dir: -1 }))} className={cn("inline-flex items-center gap-0.5 hover:text-foreground", sort?.key === key && "text-foreground")}>
        {label}{sort?.key === key && (sort.dir === -1 ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </button>
    </th>
  );

  return (
    <div className="space-y-5">
      <PageHeader title="신호 매트릭스" description={STATIC ? "데모 종목을 한 표에서 — 과열과 과매도가 색으로 한눈에 보입니다." : "관심종목을 한 표에서 — 과열과 과매도가 색으로 한눈에 보입니다."} right={<UpdatedAt at={q.data?.updated_at} />} />
      <Section
        title={<span className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs font-normal text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><i className="size-3 rounded-sm" style={tint(HOT, 0.55)} />과열 · 고점권</span>
          <span className="inline-flex items-center gap-1.5"><i className="size-3 rounded-sm" style={tint(COLD, 0.55)} />과매도 · 눌림</span>
          <span className="inline-flex items-center gap-1.5"><i className="size-3 rounded-sm" style={tint(GOLD, 0.45)} />거래량 급증</span>
          <span>색이 진할수록 극단</span>
        </span>}
        right={<Segmented size="xs" ariaLabel="상태 필터" value={filter} onChange={setFilter} options={[{ value: "all", label: "전체" }, { value: "overheated", label: "과열만" }, { value: "oversold", label: "과매도만" }]} />}
        bodyClassName="p-0">
        <QueryState q={{ ...q, isLoading: q.isLoading || !scope.ready }} label="신호 매트릭스" skeleton={<div className="p-5"><SkeletonBlock rows={10} /></div>}>
          {(d) => d.rows.length === 0 ? (
            <p className="p-5 text-sm text-muted-foreground">{d.note ?? "관심종목이 비어 있습니다 — 관심종목에 종목을 추가하면 여기에 표시됩니다."}</p>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-xs tabular">
                  <thead><tr className="border-b text-muted-foreground">
                    {head("name", "종목", undefined, false)}{head("change_pct", "현재가 · 등락")}
                    {d.columns.map((c) => head(c.key as Key, c.label, c.help))}
                    <th className="px-2 py-2 text-left font-normal">상태</th>
                  </tr></thead>
                  <tbody>{rows.map((r) => (
                    <tr key={r.symbol} className="border-b border-border/60 last:border-0 hover:bg-accent/30">
                      <td className="py-1.5 pl-5 pr-2"><Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="font-medium hover:underline">{r.name_ko ?? r.name ?? r.symbol}</Link>
                        <span className="ml-1.5 font-mono text-[10.5px] text-muted-foreground">{r.symbol}</span></td>
                      <td className="px-2 py-1.5 text-right whitespace-nowrap">{price(r.price, r.market === "KR" ? "KRW" : "USD")} <Delta value={r.change_pct} showIcon={false} className="ml-1 text-[11px]" /></td>
                      {d.columns.map((c) => {
                        const k = c.key as Key;
                        return <td key={k} style={cellStyle(k, r)} className={cn("px-2 py-1.5 text-right", k === "trend" && isNum(r.trend) && TREND_CLS[Math.max(0, Math.min(4, r.trend + 2))])}>{cellText(k, r)}</td>;
                      })}
                      <td className="px-2 py-1.5 pr-5 whitespace-nowrap">
                        {r.state === "overheated" ? <span className="rounded-full px-2 py-0.5 text-[11px] font-medium" style={tint(HOT, 0.4)}>과열</span>
                          : r.state === "oversold" ? <span title={r.oversold_reasons.join(" · ")} className="rounded-full px-2 py-0.5 text-[11px] font-medium" style={tint(COLD, 0.4)}>과매도</span>
                          : <span className="text-muted-foreground">—</span>}
                      </td>
                    </tr>))}</tbody>
                </table>
              </div>
              {rows.length === 0 && <p className="p-5 text-sm text-muted-foreground">해당하는 종목이 없습니다.</p>}
              <p className="border-t px-5 py-3 text-[11px] leading-relaxed text-muted-foreground">
                {d.legend}. 열 제목을 누르면 정렬됩니다.{d.missing.length > 0 && ` 스캔 결과가 없는 종목: ${d.missing.join(", ")} (Data unavailable)`}
              </p>
            </>
          )}
        </QueryState>
      </Section>
    </div>
  );
}
