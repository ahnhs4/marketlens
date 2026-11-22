"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ColorType, CrosshairMode, LineSeries, LineStyle, createChart, type IChartApi, type Time } from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";

import { Pill, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { num } from "@/lib/format";

type Band = "Extreme Fear" | "Fear" | "Neutral" | "Greed" | "Extreme Greed";
interface Point { date: string; value: number; label?: Band }
export interface FearGreed {
  as_of: string; value: number; label: Band; available: number; total: number;
  compare: Record<"previous" | "week" | "month" | "year", Point | null>;
  components: { key: string; label: string; score: number | null; band: Band | null; change_1w: number | null; detail: string;
    description: string; source: string; direction: string; sparkline: number[] }[];
  history: { date: string; value: number; spx: number | null }[];
  method: string; notes: string[];
  breadth_history: { state: string; message: string | null; ready: boolean; coverage_pct: number | null };
  updated_at: string;
}

// Fixed semantic colours (not the up/down price colours, which flip with the Korean convention).
export const FG_BANDS: { max: number; key: Band; ko: string; color: string }[] = [
  { max: 25, key: "Extreme Fear", ko: "극단적 공포", color: "--s4" },
  { max: 45, key: "Fear", ko: "공포", color: "--s8" },
  { max: 55, key: "Neutral", ko: "중립", color: "--muted-foreground" },
  { max: 75, key: "Greed", ko: "탐욕", color: "--s6" },
  { max: 100, key: "Extreme Greed", ko: "극단적 탐욕", color: "--s3" },
];
const bandOf = (v: number) => FG_BANDS.find((b) => v < b.max) ?? FG_BANDS[FG_BANDS.length - 1];
export const fgKo = (b?: string | null) => FG_BANDS.find((x) => x.key === b)?.ko ?? "—";
const fgColor = (b?: string | null) => `var(${FG_BANDS.find((x) => x.key === b)?.color ?? "--muted-foreground"})`;

function cssVar(name: string, fb = "#888") {
  if (typeof window === "undefined") return fb;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb;
}

export const useFearGreed = () =>
  useQuery({
    queryKey: ["fear-greed"], queryFn: () => api<FearGreed>("/api/market/fear-greed"), staleTime: 600_000,
    // While member history is being filled, poll so the full 7-indicator version replaces the partial one.
    refetchInterval: (q) => (q.state.data && !q.state.data.breadth_history.ready ? 60_000 : 1_800_000),
  });

/** Semicircle gauge: 0 (left, extreme fear) → 100 (right, extreme greed). */
export function FearGreedGauge({ value, label, size = 240 }: { value: number; label: Band; size?: number }) {
  const cx = 100, cy = 100, r = 78;
  const pt = (v: number, rr = r) => {
    const a = Math.PI * (1 - v / 100);
    return [cx + rr * Math.cos(a), cy - rr * Math.sin(a)];
  };
  const arc = (a: number, b: number) => {
    const [x1, y1] = pt(a), [x2, y2] = pt(b);
    return `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`;
  };
  const [nx, ny] = pt(value, 64);
  return (
    <svg viewBox="-10 0 220 128" width={size} className="max-w-full" role="img" aria-label={`공포·탐욕 지수 ${value.toFixed(0)} (${fgKo(label)})`}>
      {FG_BANDS.map((b, i) => {
        const lo = i ? FG_BANDS[i - 1].max : 0;
        const d = arc(lo + (lo ? 0.8 : 0), b.max - (b.max < 100 ? 0.8 : 0));
        const active = b.key === label;
        return <path key={b.key} d={d} fill="none" stroke={`var(${b.color})`} strokeWidth={active ? 20 : 15} strokeOpacity={active ? 1 : 0.35} />;
      })}
      {[0, 25, 50, 75, 100].map((t) => {
        const [x, y] = pt(t, 98);
        return <text key={t} x={x} y={y + 3} textAnchor="middle" className="fill-muted-foreground" fontSize="8">{t}</text>;
      })}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="var(--foreground)" strokeWidth={3} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={5} fill="var(--foreground)" />
      <text x={cx} y={cy + 24} textAnchor="middle" fontSize="22" fontWeight="700" className="fill-foreground tabular">{value.toFixed(0)}</text>
    </svg>
  );
}

const RANGES = [{ value: "21", label: "1개월" }, { value: "63", label: "3개월" }, { value: "126", label: "6개월" }, { value: "252", label: "1년" }, { value: "all", label: "전체" }];

function FearGreedChart({ history, height = 260 }: { history: FearGreed["history"]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { resolvedTheme } = useTheme();
  const [range, setRange] = useState("252");
  const [showSpx, setShowSpx] = useState(true);
  const [hover, setHover] = useState<{ date: string; value: number; spx: number | null } | null>(null);
  const last = history[history.length - 1];

  useEffect(() => {
    const el = ref.current;
    if (!el || !history.length) return;
    const fg = cssVar("--muted-foreground"), border = cssVar("--border");
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: fg, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      rightPriceScale: { borderColor: border, scaleMargins: { top: 0.05, bottom: 0.05 } },
      leftPriceScale: { visible: showSpx, borderColor: border },
      timeScale: { borderColor: border },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR" },
    });
    chartRef.current = chart;
    if (showSpx) {
      const spx = chart.addSeries(LineSeries, { priceScaleId: "left", color: cssVar("--s1"), lineWidth: 1, lineStyle: LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      spx.setData(history.filter((h) => h.spx !== null).map((h) => ({ time: h.date as Time, value: h.spx as number })));
    }
    const s = chart.addSeries(LineSeries, {
      lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    // Each segment takes the colour of its zone.
    s.setData(history.map((h) => ({ time: h.date as Time, value: h.value, color: cssVar(bandOf(h.value).color) })));
    for (const [p, title] of [[25, "극단적 공포"], [45, "공포"], [55, "탐욕"], [75, "극단적 탐욕"]] as const) {
      s.createPriceLine({ price: p, color: cssVar(bandOf(p - (p > 50 ? -0.1 : 0.1)).color), lineStyle: LineStyle.Dashed, lineWidth: 1,
        axisLabelVisible: false, title: p > 50 ? `${title} ↑` : `${title} ↓` });
    }
    const byDate = new Map(history.map((h) => [h.date, h]));
    chart.subscribeCrosshairMove((param) => setHover(param.time ? byDate.get(String(param.time)) ?? null : null));
    return () => { chart.remove(); chartRef.current = null; };
  }, [history, resolvedTheme, showSpx]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !history.length) return;
    if (range === "all") chart.timeScale().fitContent();
    else chart.timeScale().setVisibleLogicalRange({ from: history.length - Number(range), to: history.length + 1 });
  }, [range, history, showSpx, resolvedTheme]);

  const cur = hover ?? last;
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="tabular text-muted-foreground">
          {cur?.date} · 지수 <b style={{ color: cur ? `var(${bandOf(cur.value).color})` : undefined }}>{cur?.value.toFixed(0)}</b> ({cur ? bandOf(cur.value).ko : ""})
          {showSpx && cur?.spx ? <> · S&P 500 <b className="text-foreground">{num(cur.spx, 0)}</b></> : null}
        </span>
        <span className="flex items-center gap-2">
          <label className="inline-flex items-center gap-1 text-muted-foreground">
            <input type="checkbox" checked={showSpx} onChange={(e) => setShowSpx(e.target.checked)} />
            <span className="inline-block h-0.5 w-3 rounded bg-s1" aria-hidden />S&P 500 함께 보기 (왼쪽 축)
          </label>
          <Segmented ariaLabel="기간" size="xs" value={range} onChange={setRange} options={RANGES} />
        </span>
      </div>
      <div ref={ref} style={{ height }} />
    </div>
  );
}

function Spark({ data, color }: { data: number[]; color: string }) {
  if (data.length < 2) return null;
  const w = 120, h = 28;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - (v / 100) * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-full" preserveAspectRatio="none" aria-hidden>
      <line x1={0} x2={w} y1={h * 0.75} y2={h * 0.75} stroke="var(--border)" strokeDasharray="2 2" />
      <line x1={0} x2={w} y1={h * 0.25} y2={h * 0.25} stroke="var(--border)" strokeDasharray="2 2" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function FearGreedPanel({ showComponents = true }: { showComponents?: boolean }) {
  const q = useFearGreed();
  const d = q.data;
  return (
    <Section title={<span className="flex items-center gap-2">공포·탐욕 지수 <span className="font-normal text-muted-foreground">미국 주식시장</span></span>}
      subtitle="CNN Fear & Greed와 같은 7개 지표를 실제 시장 데이터로 직접 계산 · 0 = 극단적 공포, 100 = 극단적 탐욕"
      right={d && <UpdatedAt at={d.updated_at} source={`${d.as_of} 종가 기준`} />}>
      <QueryState q={q} label="공포·탐욕 지수" skeleton={<SkeletonBlock rows={8} />}>
        {(d) => (
          <div className="space-y-4">
            {!d.breadth_history.ready && (
              <Pill tone="warn">{d.available}/{d.total}개 지표로 계산 중 - 주가 강도·시장 폭은 S&P 500 구성 종목 3년 이력을 받는 중이며 끝나면 자동 반영 ({d.breadth_history.message ?? "대기"})</Pill>
            )}
            <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
              <div className="flex flex-col items-center">
                <FearGreedGauge value={d.value} label={d.label} />
                <div className="-mt-1 text-lg font-semibold" style={{ color: fgColor(d.label) }}>{fgKo(d.label)}</div>
                <dl className="mt-3 w-full space-y-1 text-xs">
                  {([["previous", "전일"], ["week", "1주 전"], ["month", "1개월 전"], ["year", "1년 전"]] as const).map(([k, l]) => {
                    const c = d.compare[k];
                    return (
                      <div key={k} className="flex items-center justify-between border-b border-dashed border-border/60 pb-1">
                        <dt className="text-muted-foreground">{l}</dt>
                        <dd className="tabular">{c ? <><span style={{ color: fgColor(c.label) }}>{fgKo(c.label)}</span> <b>{c.value.toFixed(0)}</b></> : "—"}</dd>
                      </div>
                    );
                  })}
                </dl>
              </div>
              <FearGreedChart history={d.history} />
            </div>
            {showComponents && (
              <div>
                <h3 className="mb-2 text-xs font-semibold text-muted-foreground">구성 지표 7개 (각 0~100, 최근 1년 평균 대비 위치)</h3>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {d.components.map((c) => (
                    <div key={c.key} className={cn("rounded-lg border p-2.5", c.score === null && "border-dashed opacity-80")} title={`${c.description} · 출처 ${c.source}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-sm font-medium">{c.label}</div>
                          <div className="text-[10px] text-muted-foreground">{c.direction}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-lg font-semibold tabular" style={{ color: fgColor(c.band) }}>{c.score === null ? "—" : c.score.toFixed(0)}</div>
                          <div className="text-[10px]" style={{ color: fgColor(c.band) }}>{c.band ? fgKo(c.band) : "준비 중"}
                            {c.change_1w !== null && <span className="ml-1 text-muted-foreground">1주 {c.change_1w > 0 ? "+" : ""}{c.change_1w.toFixed(0)}</span>}</div>
                        </div>
                      </div>
                      {c.score !== null && (
                        <div className="relative mt-1.5 h-1.5 rounded-full bg-muted" aria-hidden>
                          <div className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card" style={{ left: `${c.score}%`, background: fgColor(c.band) }} />
                        </div>
                      )}
                      <Spark data={c.sparkline} color={fgColor(c.band)} />
                      <p className="text-[11px] leading-snug">{c.detail}</p>
                    </div>
                  ))}
                  <div className="rounded-lg border border-dashed p-2.5 text-[11px] text-muted-foreground">
                    <div className="mb-1 font-medium text-foreground">계산 방법</div>
                    <p>{d.method}</p>
                    <ul className="mt-1 space-y-0.5">{d.notes.map((n) => <li key={n}>· {n}</li>)}</ul>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Section>
  );
}

/** Small tile for the dashboard's key-number row. */
export function FearGreedMini() {
  const q = useFearGreed();
  const d = q.data;
  const week = useMemo(() => d?.compare.week, [d]);
  if (!d) return <span className="text-muted-foreground">{q.isError ? "데이터를 불러올 수 없습니다" : "계산 중…"}</span>;
  return (
    <span className="tabular">
      <b className="text-2xl" style={{ color: fgColor(d.label) }}>{d.value.toFixed(0)}</b>{" "}
      <span style={{ color: fgColor(d.label) }}>{fgKo(d.label)}</span>
      {week && <span className="block text-xs text-muted-foreground">1주 전 {week.value.toFixed(0)} ({fgKo(week.label)})</span>}
    </span>
  );
}
