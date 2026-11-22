"use client";

import { AreaSeries, ColorType, LineSeries, createChart, type Time } from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useRef } from "react";

/** `date`: 'YYYY-MM-DD', or unix seconds for intraday series. */
export interface LineDef { name: string; color: string; points: { date: string | number; value: number }[]; width?: 1 | 2 | 3 }

function cssVar(name: string, fb = "#888") {
  if (typeof window === "undefined") return fb;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb;
}

/** Time-series line chart (one axis, any number of same-unit series) with crosshair + legend. */
export function LineChart({ lines, height = 260, area = false, format }: { lines: LineDef[]; height?: number; area?: boolean; format?: (v: number) => string }) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolvedTheme } = useTheme();
  useEffect(() => {
    const el = ref.current;
    if (!el || !lines.length) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--muted-foreground"), fontSize: 11, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--grid", "rgba(128,128,128,.1)") } },
      rightPriceScale: { borderColor: cssVar("--border") },
      timeScale: { borderColor: cssVar("--border"), timeVisible: typeof lines[0]?.points[0]?.date === "number" },
      localization: { locale: "ko-KR", ...(format ? { priceFormatter: format } : {}) },
    });
    for (const l of lines) {
      const color = l.color.startsWith("--") ? cssVar(l.color) : l.color;
      const s = area && lines.length === 1
        ? chart.addSeries(AreaSeries, { lineColor: color, topColor: `${color}33`, bottomColor: `${color}05`, lineWidth: 2, priceLineVisible: false })
        : chart.addSeries(LineSeries, { color, lineWidth: l.width ?? 2, priceLineVisible: false, lastValueVisible: true, title: lines.length > 1 ? l.name : "" });
      const seen = new Set<string | number>();
      s.setData(l.points.filter((p) => !seen.has(p.date) && seen.add(p.date)).map((p) => ({ time: p.date as Time, value: p.value })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [lines, resolvedTheme, area, format]);
  return (
    <div>
      {lines.length > 1 && (
        <div className="mb-1 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
          {lines.map((l) => <span key={l.name} className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-3 rounded" style={{ background: l.color.startsWith("--") ? `var(${l.color})` : l.color }} />{l.name}</span>)}
        </div>
      )}
      <div ref={ref} style={{ height }} />
    </div>
  );
}
