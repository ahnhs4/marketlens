"use client";

import { useMutation } from "@tanstack/react-query";
import { Plus, Search, X } from "lucide-react";
import { useEffect, useState } from "react";

import { PageHeader, Pill, Section, Segmented, SkeletonBlock, Unavailable } from "@/components/common";
import { ExcludedNote } from "@/components/excluded-note";
import { COLS, RankTable } from "@/components/rank-table";
import { post } from "@/lib/api";
import { universeKo } from "@/lib/i18n";
import type { ScanMeta, ScanRow } from "@/lib/types";

const FIELDS: { value: string; label: string; unit?: string; scale?: number }[] = [
  { value: "market_cap", label: "시가총액 (USD)", unit: "십억 달러", scale: 1e9 }, { value: "price", label: "주가" },
  { value: "pe", label: "PER" }, { value: "forward_pe", label: "선행 PER" },
  { value: "revenue_growth", label: "매출 성장률", unit: "%", scale: 0.01 }, { value: "eps_growth", label: "EPS 성장률", unit: "%", scale: 0.01 },
  { value: "rsi", label: "RSI" }, { value: "mom20", label: "20일 수익률", unit: "%", scale: 0.01 }, { value: "mom60", label: "60일 수익률", unit: "%", scale: 0.01 },
  { value: "vol_ratio", label: "거래량 / 20일 평균", unit: "배" }, { value: "dist_sma200", label: "200일선 대비 괴리", unit: "%", scale: 0.01 },
  { value: "dist_52w_high", label: "52주 고점 대비", unit: "%", scale: 0.01 }, { value: "atr_pct", label: "ATR (주가 대비 %)" },
  { value: "change_pct", label: "당일 등락률", unit: "%" },
  { value: "technical", label: "기술적 점수" }, { value: "macro", label: "매크로 점수" }, { value: "overall", label: "종합 점수" },
  { value: "pbr", label: "PBR (국내 · DART)", unit: "배" }, { value: "roe", label: "ROE (국내 · DART)", unit: "%" },
  { value: "fin_score", label: "재무 점수 (국내 · DART)" },
];
const OPS = [">", ">=", "<", "<="] as const;
interface F { field: string; op: (typeof OPS)[number]; value: string }

const PRESETS: Record<string, { filters: F[]; above_sma200?: boolean; sma20_gt_sma60?: boolean; near_breakout?: boolean }> = {
  "모멘텀 주도주": { filters: [{ field: "market_cap", op: ">", value: "10" }, { field: "rsi", op: ">", value: "55" }, { field: "mom20", op: ">", value: "5" }, { field: "vol_ratio", op: ">", value: "1.2" }, { field: "technical", op: ">", value: "70" }], above_sma200: true },
  "상승 추세 속 눌림목": { filters: [{ field: "rsi", op: "<", value: "45" }, { field: "dist_sma200", op: ">", value: "5" }], above_sma200: true },
  "돌파 임박": { filters: [{ field: "technical", op: ">", value: "60" }], near_breakout: true, sma20_gt_sma60: true },
  "매크로 + 기술적": { filters: [{ field: "macro", op: ">", value: "60" }, { field: "technical", op: ">", value: "62" }] },
  "추세 있는 가치주": { filters: [{ field: "pe", op: "<", value: "18" }, { field: "pe", op: ">", value: "0" }], above_sma200: true },
};

type Resp = ScanMeta & { items: ScanRow[]; matches: number; total_scanned: number; field_coverage_pct: Record<string, number>; excluded_missing_data: string[] };

export default function ScreenerPage() {
  const [uni, setUni] = useState("ALL");
  const [filters, setFilters] = useState<F[]>(PRESETS["모멘텀 주도주"].filters);
  const [flags, setFlags] = useState<{ above_sma200?: boolean; sma20_gt_sma60?: boolean; near_breakout?: boolean }>({ above_sma200: true });
  const run = useMutation({
    mutationFn: () => post<Resp>("/api/screener", {
      universe: uni, limit: 200, sort: "technical",
      filters: filters.filter((f) => f.value !== "" && !Number.isNaN(Number(f.value))).map((f) => {
        const def = FIELDS.find((x) => x.value === f.field);
        return { field: f.field, op: f.op, value: Number(f.value) * (def?.scale ?? 1) };
      }),
      ...Object.fromEntries(Object.entries(flags).map(([k, v]) => [k, v ? true : null])),
    }),
  });
  useEffect(() => { run.mutate(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (i: number, patch: Partial<F>) => setFilters((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <div className="space-y-5">
      <PageHeader title="종목 스크리너" description="스캔된 종목군을 주가 · 펀더멘털 · 기술적 지표 · 점수로 걸러냅니다." />
      <Section title="조건" right={<Segmented size="xs" value={uni} onChange={setUni} options={["ALL", "SP500", "NDX", "KOSPI", "KOSDAQ", "WATCHLIST"].map((u) => ({ value: u, label: universeKo(u) }))} />}>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {Object.entries(PRESETS).map(([k, p]) => (
            <button key={k} className="rounded-md border px-2 py-1 text-xs hover:bg-accent"
              onClick={() => { setFilters(p.filters); setFlags({ above_sma200: p.above_sma200, sma20_gt_sma60: p.sma20_gt_sma60, near_breakout: p.near_breakout }); }}>{k}</button>
          ))}
        </div>
        <div className="space-y-2">
          {filters.map((f, i) => {
            const def = FIELDS.find((x) => x.value === f.field);
            return (
              <div key={i} className="flex flex-wrap items-center gap-2">
                <select value={f.field} onChange={(e) => set(i, { field: e.target.value })} className="h-8 rounded-md border bg-background px-2 text-sm" aria-label="항목">
                  {FIELDS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
                </select>
                <select value={f.op} onChange={(e) => set(i, { op: e.target.value as F["op"] })} className="h-8 rounded-md border bg-background px-2 text-sm" aria-label="연산자">
                  {OPS.map((o) => <option key={o}>{o}</option>)}
                </select>
                <input value={f.value} onChange={(e) => set(i, { value: e.target.value })} inputMode="decimal" className="h-8 w-24 rounded-md border bg-background px-2 text-sm tabular" aria-label="값" />
                <span className="text-xs text-muted-foreground">{def?.unit ?? ""}</span>
                <button onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))} className="rounded p-1 text-muted-foreground hover:bg-accent" aria-label="조건 삭제"><X className="size-4" /></button>
              </div>
            );
          })}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button onClick={() => setFilters((fs) => [...fs, { field: "rsi", op: ">", value: "50" }])} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"><Plus className="size-3.5" />조건 추가</button>
            {(["above_sma200", "sma20_gt_sma60", "near_breakout"] as const).map((k) => (
              <label key={k} className="inline-flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={!!flags[k]} onChange={(e) => setFlags((s) => ({ ...s, [k]: e.target.checked }))} />
                {k === "above_sma200" ? "주가 > 200일선" : k === "sma20_gt_sma60" ? "20일선 > 60일선" : "55일 고점 3% 이내"}
              </label>
            ))}
            <button onClick={() => run.mutate()} className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"><Search className="size-4" />검색 실행</button>
          </div>
        </div>
      </Section>
      <Section title="결과" right={run.data && <span>{run.data.total_scanned}종목 중 {run.data.matches}종목 일치</span>}>
        {run.isPending ? <SkeletonBlock rows={8} /> : run.isError ? <Unavailable message={(run.error as Error).message} onRetry={() => run.mutate()} label="스크리너" /> : run.data ? (
          <>
            {run.data.note && <p className="mb-2 text-sm text-warn">{run.data.note}</p>}
            <div className="mb-2 flex flex-wrap gap-1.5 text-[11px]">
              {Object.entries(run.data.field_coverage_pct).map(([k, v]) => <Pill key={k} tone={v < 50 ? "warn" : "default"}>{FIELDS.find((x) => x.value === k)?.label ?? k} 데이터 보유: 전체의 {v}%</Pill>)}
              <ExcludedNote e={run.data.excluded_overbought} />
              {run.data.excluded_missing_data.length > 0 && <Pill tone="warn">{run.data.excluded_missing_data.map((k) => FIELDS.find((x) => x.value === k)?.label ?? k).join(", ")} 데이터가 없는 종목은 제외됨 (임의 값으로 채우지 않음)</Pill>}
            </div>
            {run.data.items.length === 0 ? <p className="text-sm text-muted-foreground">모든 조건을 만족하는 종목이 없습니다.</p> : (
              <RankTable rows={run.data.items} initialSort="technical" cols={[COLS.symbol, COLS.score, COLS.macro, COLS.fin, COLS.signal, COLS.change, COLS.mcap, COLS.pe, COLS.pbr, COLS.roe, COLS.rsi, COLS.mom, COLS.vol, COLS.trend, COLS.reason]} />
            )}
          </>
        ) : null}
      </Section>
    </div>
  );
}
