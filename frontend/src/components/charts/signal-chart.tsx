"use client";

import { AreaSeries, ColorType, LineSeries, LineStyle, createChart, createSeriesMarkers, type SeriesMarker, type Time } from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useRef } from "react";

import { cssVar, type LineDef } from "./line-chart";

/** A shaded date range, e.g. an S&P 500 decline from peak to trough. */
export interface Band { from: string; to: string }
/** A marker on the first line; `date` must be one of its points. */
export interface Mark { date: string; text: string }

function alpha(color: string, a: number) {
  if (/^#[0-9a-f]{6}$/i.test(color)) return color + Math.round(a * 255).toString(16).padStart(2, "0");
  const m = color.match(/^rgba?\(([^)]+)\)$/);
  if (m) { const [r, g, b] = m[1].split(",").map((x) => x.trim()); return `rgba(${r}, ${g}, ${b}, ${a})`; }
  return color;
}

/** Same-unit lines with optional shaded ranges, dashed threshold lines and markers on the first line. */
export function SignalChart({ lines, bands = [], marks = [], thresholds = [], height = 260, format }: {
  lines: LineDef[]; bands?: Band[]; marks?: Mark[]; thresholds?: { value: number; label: string }[]; height?: number; format?: (v: number) => string;
}) {
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
      timeScale: { borderColor: cssVar("--border") },
      // the page is long: the mouse wheel scrolls the page instead of zooming the chart (drag and pinch still work)
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
      localization: { locale: "ko-KR", ...(format ? { priceFormatter: format } : {}) },
    });
    if (bands.length) {   // a 0/1 area on a hidden full-height scale, drawn first so the lines sit on top
      const fill = alpha(cssVar("--down", "#cf4a57"), 0.13);
      const shade = chart.addSeries(AreaSeries, { priceScaleId: "bands", lineColor: "transparent", lineWidth: 1, topColor: fill, bottomColor: fill,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      chart.priceScale("bands").applyOptions({ scaleMargins: { top: 0, bottom: 0 } });
      const days = [...new Set(lines.flatMap((l) => l.points.map((p) => String(p.date))))].sort();
      shade.setData(days.map((d) => ({ time: d as Time, value: bands.some((b) => d >= b.from && d <= b.to) ? 1 : 0 })));
    }
    lines.forEach((l, i) => {
      const color = l.color.startsWith("--") ? cssVar(l.color) : l.color;
      const s = chart.addSeries(LineSeries, { color, lineWidth: l.width ?? 2, priceLineVisible: false, lastValueVisible: true, title: lines.length > 1 ? l.name : "" });
      const seen = new Set<string | number>();
      s.setData(l.points.filter((p) => !seen.has(p.date) && seen.add(p.date)).map((p) => ({ time: p.date as Time, value: p.value })));
      if (i > 0) return;
      for (const t of thresholds) s.createPriceLine({ price: t.value, color: cssVar("--foreground"), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: t.label });
      if (marks.length) {
        const ms: SeriesMarker<Time>[] = [...marks].sort((a, b) => a.date.localeCompare(b.date))
          .map((m) => ({ time: m.date as Time, position: "aboveBar", color: cssVar("--foreground"), shape: "arrowDown", text: m.text }));
        createSeriesMarkers(s, ms);
      }
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [lines, bands, marks, thresholds, resolvedTheme, format]);
  return (
    <div>
      <div className="mb-1 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {lines.length > 1 && lines.map((l) => <span key={l.name} className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-3 rounded" style={{ background: l.color.startsWith("--") ? `var(${l.color})` : l.color }} />{l.name}</span>)}
        {bands.length > 0 && <span className="inline-flex items-center gap-1"><span className="inline-block h-2.5 w-3 rounded-sm bg-down/15" />S&amp;P 500 10% 이상 하락 구간</span>}
      </div>
      <div ref={ref} style={{ height }} />
    </div>
  );
}
