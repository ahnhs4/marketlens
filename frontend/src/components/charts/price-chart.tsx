"use client";

import {
  BaselineSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";

import { num, price as fmtPrice } from "@/lib/format";
import { patternKo } from "@/lib/i18n";
import type { Bar, Level, PatternEvent, RsiPeriods, Trendline, VolumeProfile } from "@/lib/types";

export type IndicatorKey =
  | "sma5" | "sma20" | "sma60" | "sma120" | "sma200" | "ema20" | "bb" | "vwap" | "volume" | "volume_profile"
  | "osc3" | "rsi" | "macd" | "stoch" | "atr" | "adx" | "obv" | "levels";

export type OscMode = "stoch" | "rsi";
export type StochSets = Record<"short" | "medium" | "long", number[]>;
const HZ = ["short", "medium", "long"] as const;
const HZ_KO = { short: "단기", medium: "중기", long: "장기" } as const;
const RSI_COL = { short: "rsi_short", medium: "rsi", long: "rsi_long" } as const;
/** Column keys, zone thresholds and label for one horizon pane of the three-pane oscillator. */
function oscSpec(mode: OscMode, hz: (typeof HZ)[number], sets?: StochSets, rsi?: RsiPeriods) {
  if (mode === "rsi") {
    const k = RSI_COL[hz];
    return { k, d: `${k}_sig`, hi: 70, lo: 30, name: `${HZ_KO[hz]} RSI(${(rsi ?? { short: 7, medium: 14, long: 28 })[hz]})`, kLabel: "RSI", dLabel: "시그널" };
  }
  const n = (sets ?? { short: [5, 3, 3], medium: [10, 6, 6], long: [20, 12, 12] })[hz];
  return { k: `stoch_${hz}_k`, d: `stoch_${hz}_d`, hi: 80, lo: 20, name: `${HZ_KO[hz]} 스토캐스틱(${n.join(",")})`, kLabel: "%K", dLabel: "%D" };
}

// Fixed categorical order (validated palette): MA identity never changes with toggles.
const MA_COLORS: Record<string, string> = { sma5: "--s5", sma20: "--s1", sma50: "--s6", sma60: "--s2", sma120: "--s3", sma200: "--s4", ema20: "--s7", vwap: "--s7" };
const MA_KEYS = ["sma5", "sma20", "sma50", "sma60", "sma120", "sma200", "ema20", "vwap"];
// RSI by horizon: colour + line style so the three stay distinguishable without relying on colour alone.
const RSI_LINES = [
  { key: "rsi_short", h: "short", label: "단기", color: "--s2", width: 1, style: LineStyle.Solid },
  { key: "rsi", h: "medium", label: "중기", color: "--s7", width: 2, style: LineStyle.Solid },
  { key: "rsi_long", h: "long", label: "장기", color: "--s1", width: 2, style: LineStyle.Dashed },
] as const;
export const OSC_H = 100;  // px per 단기/중기/장기 pane
const PANE_NAMES: Record<string, string> = { macd: "MACD", stoch: "스토캐스틱", atr: "ATR", adx: "ADX / DMI", obv: "OBV" };

export interface Cross { time: string | number; kind: "golden" | "death"; price: number; fast: number; slow: number; index: number }

/** Golden cross = fast MA closes above slow MA after being below; death cross = the reverse. Uses only bar values. */
export function findCrosses(bars: Bar[], fast: number, slow: number): Cross[] {
  const out: Cross[] = [];
  const fk = `sma${fast}`, sk = `sma${slow}`;
  for (let i = 1; i < bars.length; i++) {
    const a0 = bars[i - 1][fk], b0 = bars[i - 1][sk], a1 = bars[i][fk], b1 = bars[i][sk];
    if (typeof a0 !== "number" || typeof b0 !== "number" || typeof a1 !== "number" || typeof b1 !== "number") continue;
    const d0 = a0 - b0, d1 = a1 - b1;
    if (d0 <= 0 && d1 > 0) out.push({ time: bars[i].time, kind: "golden", price: a1, fast, slow, index: i });
    else if (d0 >= 0 && d1 < 0) out.push({ time: bars[i].time, kind: "death", price: a1, fast, slow, index: i });
  }
  return out;
}

function cssVar(name: string, fallback = "#888") {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function withAlpha(hex: string, a: number) {
  const m = hex.replace("#", "");
  if (m.length !== 6) return hex;
  const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}

function tzOffsetSec(ts: number, tz: string) {
  const d = new Date(ts * 1000);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(d);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUTC = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return Math.round((asUTC - d.getTime()) / 1000);
}

interface Props {
  bars: Bar[];
  intraday: boolean;
  currency?: string | null;
  exchangeTz: string;
  enabled: Record<string, boolean>;
  levels?: { supports: Level[]; resistances: Level[] } | null;
  volumeProfile?: VolumeProfile | null;
  patterns?: PatternEvent[];
  cross?: [number, number] | null;
  rsiPeriods?: RsiPeriods;
  oscMode?: OscMode;
  stochSets?: StochSets;
  height?: number;
  /** Trendlines to draw (from the chart API), most credible first. */
  trendlines?: Trendline[];
  /** Open on this bar (the selected range); older bars stay loaded so zooming out never runs out. */
  visibleFrom?: string | number | null;
  /** Dashed line at the selected range's high, labelled with how far the price is below it. */
  peak?: { price: number; pct: number | null; time: string | number } | null;
}

type Row = Bar & { t: Time };

interface Legend { o?: number; h?: number; l?: number; c?: number; v?: number | null; chg?: number; vals: Record<string, number | null> }

export function PriceChart({ peak, bars, intraday, currency, exchangeTz, enabled, levels, volumeProfile, patterns, cross, rsiPeriods,
  oscMode = "stoch", stochSets, height = 560, visibleFrom, trendlines }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const { resolvedTheme } = useTheme();
  const [legend, setLegend] = useState<Legend>({ vals: {} });
  const [paneTops, setPaneTops] = useState<number[]>([]);

  const data = useMemo<Row[]>(() => {
    const t = (b: Bar): Time => (intraday ? ((Number(b.time) + tzOffsetSec(Number(b.time), exchangeTz)) as UTCTimestamp) : (b.time as Time));
    return bars.map((b) => ({ ...b, t: t(b) }) as Row);
  }, [bars, intraday, exchangeTz]);

  const stochKey = JSON.stringify(stochSets ?? null), rsiKey = JSON.stringify(rsiPeriods ?? null);
  // "osc3" expands into three panes (단기 / 중기 / 장기), like an HTS lower-indicator stack.
  const subPanes: string[] = (["osc3", "rsi", "macd", "stoch", "atr", "adx", "obv"] as const)
    .filter((k) => enabled[k]).flatMap((k) => (k === "osc3" ? HZ.map((h) => `osc_${h}`) : [k]));

  useEffect(() => {
    const el = wrap.current;
    if (!el || data.length === 0) return;
    const fg = cssVar("--muted-foreground"), grid = cssVar("--grid", "rgba(128,128,128,0.1)"), border = cssVar("--border");
    const up = cssVar("--up-mark", "#0ca30c"), down = cssVar("--down-mark", "#d03b3b");
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: fg, fontSize: 11, attributionLogo: false,
        panes: { separatorColor: border, separatorHoverColor: border, enableResize: true } },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border, scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: { borderColor: border, timeVisible: intraday, secondsVisible: false, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "ko-KR", priceFormatter: (p: number) => fmtPrice(p, currency) },
    });
    chartRef.current = chart;

    const session = (b: (typeof data)[number]) => (b.session as string | undefined) ?? "regular";
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
      priceLineVisible: true, lastValueVisible: true,
    });
    candle.setData(data.map((b) => {
      const ext = intraday && session(b) !== "regular";
      const col = b.close >= b.open ? up : down;
      return { time: b.t, open: b.open, high: b.high, low: b.low, close: b.close,
        ...(ext ? { color: withAlpha(col, 0.35), wickColor: withAlpha(col, 0.35), borderColor: withAlpha(col, 0.35) } : {}) };
    }));
    candleRef.current = candle;

    if (enabled.volume) {
      const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(data.map((b) => ({ time: b.t, value: b.volume ?? 0, color: withAlpha(b.close >= b.open ? up : down, 0.35) })));
    }
    const line = (key: string, color: string, pane = 0, width: 1 | 2 = 1, style: LineStyle = LineStyle.Solid) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, pane);
      s.setData(data.filter((b) => b[key] !== null && b[key] !== undefined).map((b) => ({ time: b.t, value: Number(b[key]) })));
      return s;
    };
    // Moving averages; the pair used for golden/death crosses is always drawn, thicker, so the crossing is visible.
    const crossKeys = cross ? [`sma${cross[0]}`, `sma${cross[1]}`] : [];
    const maSeries: Record<string, ReturnType<typeof line>> = {};
    for (const k of MA_KEYS) {
      const isCross = crossKeys.includes(k);
      if (enabled[k] || isCross) maSeries[k] = line(k, cssVar(MA_COLORS[k]), 0, isCross || k === "sma200" ? 2 : 1);
    }
    if (enabled.bb) {
      const c = withAlpha(cssVar("--muted-foreground"), 0.7);
      line("bb_upper", c, 0, 1, LineStyle.Dotted);
      line("bb_mid", withAlpha(cssVar("--muted-foreground"), 0.4), 0, 1, LineStyle.Dotted);
      line("bb_lower", c, 0, 1, LineStyle.Dotted);
    }

    let pane = 1;
    const paneOf: Record<string, number> = {};
    for (const k of subPanes) {
      paneOf[k] = pane;
      if (k.startsWith("osc_")) {
        const sp = oscSpec(oscMode, k.slice(4) as (typeof HZ)[number], stochSets, rsiPeriods);
        const fixed = () => ({ priceRange: { minValue: 0, maxValue: 100 } });
        const pf = { type: "price" as const, precision: 0, minMove: 1 };
        const pts = data.filter((b) => typeof b[sp.k] === "number").map((b) => ({ time: b.t, value: Number(b[sp.k]) }));
        const hot = cssVar("--s2"), cold = cssVar("--s1"), none = "rgba(0,0,0,0)";
        // Shaded zones: fill between %K and the threshold only where %K is beyond it.
        const ob = chart.addSeries(BaselineSeries, { baseValue: { type: "price", price: sp.hi }, topLineColor: none, bottomLineColor: none,
          topFillColor1: withAlpha(hot, 0.55), topFillColor2: withAlpha(hot, 0.35), bottomFillColor1: none, bottomFillColor2: none,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: fixed, priceFormat: pf }, pane);
        ob.setData(pts);
        const os = chart.addSeries(BaselineSeries, { baseValue: { type: "price", price: sp.lo }, topLineColor: none, bottomLineColor: none,
          topFillColor1: none, topFillColor2: none, bottomFillColor1: withAlpha(cold, 0.35), bottomFillColor2: withAlpha(cold, 0.55),
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: fixed, priceFormat: pf }, pane);
        os.setData(pts);
        const kLine = chart.addSeries(LineSeries, { color: cssVar("--s8"), lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
          crosshairMarkerVisible: false, autoscaleInfoProvider: fixed, priceFormat: pf }, pane);
        kLine.setData(pts);
        const dLine = chart.addSeries(LineSeries, { color: cssVar("--s1"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false, autoscaleInfoProvider: fixed, priceFormat: pf }, pane);
        dLine.setData(data.filter((b) => typeof b[sp.d] === "number").map((b) => ({ time: b.t, value: Number(b[sp.d]) })));
        kLine.createPriceLine({ price: sp.hi, color: withAlpha(hot, 0.8), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "" });
        kLine.createPriceLine({ price: sp.lo, color: withAlpha(cold, 0.8), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "" });
        kLine.createPriceLine({ price: 50, color: withAlpha(fg, 0.25), lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
        // Turn markers: %K crossing %D inside the extreme zones (상승 전환 / 하락 전환).
        const marks: SeriesMarker<Time>[] = [];
        for (let i = 1; i < data.length; i++) {
          const k0 = data[i - 1][sp.k], d0 = data[i - 1][sp.d], k1 = data[i][sp.k], d1 = data[i][sp.d];
          if (typeof k0 !== "number" || typeof d0 !== "number" || typeof k1 !== "number" || typeof d1 !== "number") continue;
          if (k0 <= d0 && k1 > d1 && k1 <= sp.lo + 10) marks.push({ time: data[i].t, position: "belowBar", color: up, shape: "arrowUp", size: 0.8 });
          else if (k0 >= d0 && k1 < d1 && k1 >= sp.hi - 10) marks.push({ time: data[i].t, position: "aboveBar", color: down, shape: "arrowDown", size: 0.8 });
        }
        if (marks.length) createSeriesMarkers(kLine, marks);
      } else if (k === "rsi") {
        let s: ReturnType<typeof line> | null = null;
        for (const r of RSI_LINES) {
          const ls = line(r.key, cssVar(r.color), pane, r.width, r.style);
          if (r.key === "rsi") s = ls;
        }
        s!.createPriceLine({ price: 70, color: withAlpha(down, 0.7), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "과매수" });
        s!.createPriceLine({ price: 30, color: withAlpha(up, 0.7), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "과매도" });
        s!.createPriceLine({ price: 50, color: withAlpha(fg, 0.3), lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
      } else if (k === "macd") {
        const h = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
        h.setData(data.filter((b) => b.macd_hist !== null).map((b) => ({ time: b.t, value: Number(b.macd_hist), color: withAlpha(Number(b.macd_hist) >= 0 ? up : down, 0.55) })));
        line("macd", cssVar("--s1"), pane);
        line("macd_signal", cssVar("--s2"), pane);
      } else if (k === "stoch") {
        line("stoch_k", cssVar("--s1"), pane);
        line("stoch_d", cssVar("--s2"), pane);
      } else if (k === "atr") {
        line("atr", cssVar("--s4"), pane);
      } else if (k === "adx") {
        line("adx", cssVar("--foreground"), pane, 2);
        line("plus_di", up, pane);
        line("minus_di", down, pane);
      } else if (k === "obv") {
        line("obv", cssVar("--s3"), pane);
      }
      pane++;
    }
    const panes = chart.panes();
    const subH = subPanes.length ? 96 : 0;
    // Stretch factors are relative, so set every pane at once (sequential setHeight calls steal height from
    // each other). The RSI pane carries three lines, so it gets more room than the other lower panes.
    const subHeights = subPanes.map((k) => (k === "rsi" ? Math.round(subH * 1.45) : k.startsWith("osc_") ? OSC_H : subH));
    const mainH = Math.max(200, height - 30 - subHeights.reduce((a, b) => a + b, 0));
    panes.forEach((p, i) => p.setStretchFactor(i === 0 ? mainH : subHeights[i - 1] ?? subH));

    if (enabled.levels && levels) {
      for (const lv of [...levels.supports, ...levels.resistances]) {
        candle.createPriceLine({ price: lv.price, color: withAlpha(lv.label.startsWith("S") ? up : down, lv.projected ? 0.35 : 0.75),
          lineWidth: 1, lineStyle: lv.projected ? LineStyle.SparseDotted : LineStyle.Dashed, axisLabelVisible: true,
          title: `${lv.label.startsWith("S") ? "지지" : "저항"}${lv.label.slice(1)}` });
      }
    }
    if (enabled.drawdown && peak) {
      candle.createPriceLine({ price: peak.price, color: withAlpha(fg, 0.55), lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true,
        title: `고점${peak.pct !== null ? ` ${peak.pct.toFixed(1)}%` : ""}` });
    }
    if (enabled.volume_profile && volumeProfile) {
      candle.createPriceLine({ price: volumeProfile.poc, color: withAlpha(cssVar("--s4"), 0.8), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "매물대 POC" });
    }

    const markers: SeriesMarker<Time>[] = [];
    if (!intraday) {
      const dates = new Set(data.map((b) => String(b.t)));
      for (const p of patterns ?? []) {
        if (p.direction === "neutral" || !dates.has(p.date)) continue;
        markers.push({ time: p.date as Time, position: p.direction === "bullish" ? "belowBar" : "aboveBar",
          color: withAlpha(p.direction === "bullish" ? up : down, 0.8), shape: "square", size: 0.6,
          text: `${patternKo(p.pattern)}${p.status === "forming" ? "(형성 중)" : ""}` });
      }
    }
    if (enabled.drawdown && peak) {
      // The bar that made the high (matched on price, so daily, weekly and intraday views all find it).
      let bi = -1;
      data.forEach((b, i) => { if (bi < 0 || Math.abs(b.high - peak.price) < Math.abs(data[bi].high - peak.price)) bi = i; });
      if (bi >= 0 && Math.abs(data[bi].high / peak.price - 1) < 0.002) {
        markers.push({ time: data[bi].t, position: "aboveBar", color: withAlpha(fg, 0.9), shape: "arrowDown", size: 1, text: "고점" });
      }
    }
    if (cross) {
      // Large labelled arrows on the candles + a dot exactly where the two averages meet.
      const crosses = findCrosses(data, cross[0], cross[1]);
      const dots: SeriesMarker<Time>[] = [];
      // Label the most recent crosses; older ones keep the arrow so long ranges stay readable.
      const labelFrom = crosses.length - 8;
      crosses.forEach((c, i) => {
        const t = data[c.index].t;
        const golden = c.kind === "golden";
        markers.push({ time: t, position: golden ? "belowBar" : "aboveBar", color: golden ? up : down,
          shape: golden ? "arrowUp" : "arrowDown", size: 2, text: i >= labelFrom ? (golden ? "골든크로스" : "데드크로스") : "" });
        dots.push({ time: t, position: "atPriceMiddle", price: c.price, color: golden ? up : down, shape: "circle", size: 1.6 });
      });
      const fastSeries = maSeries[`sma${cross[0]}`];
      if (fastSeries && dots.length) createSeriesMarkers(fastSeries, dots);
    }
    if (enabled.trendlines !== false && trendlines?.length) {
      // Each line: straight segment from its first anchor to today, touch points as dots, label on the price axis.
      const tOf = new Map(data.map((d) => [String(d.time), d.t]));
      const iOf = new Map(data.map((d, i) => [String(d.time), i]));
      const labelled = new Set<string>();   // price-axis label only on the most credible line of each side
      trendlines.forEach((tl) => {
        const showLabel = !labelled.has(tl.kind);
        labelled.add(tl.kind);
        const i0 = iOf.get(String(tl.start.time)), i1 = iOf.get(String(tl.end.time));
        if (i0 === undefined || i1 === undefined || i1 <= i0) return;
        // pure white on the dark theme, pure black on the light one - trendlines must stand out from candles and MAs
        const ink = resolvedTheme === "light" ? "#000000" : "#ffffff";
        const color = withAlpha(ink, tl.confirmed ? 1 : 0.7);
        const s = chart.addSeries(LineSeries, { color, lineWidth: tl.confirmed ? 2 : 1, lineStyle: tl.confirmed ? LineStyle.Solid : LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: showLabel, crosshairMarkerVisible: false,
          title: showLabel ? `추세 ${tl.kind === "support" ? "지지" : "저항"} ${tl.touches}회` : "" }, 0);
        // a point on every bar (straight in bar space, like the chart's x-axis) so touch dots sit on the line
        const step = (tl.end.value - tl.start.value) / (i1 - i0);
        s.setData(data.slice(i0, i1 + 1).map((d, k) => ({ time: d.t, value: tl.start.value + step * k })));
        const dots = tl.touch_points.map((p) => tOf.get(String(p.time))).map((t, i) => (t === undefined ? null : {
          time: t, position: "atPriceMiddle" as const, price: tl.touch_points[i].value, color, shape: "circle" as const, size: 0.9 })).filter((x) => x !== null);
        if (dots.length) createSeriesMarkers(s, dots as SeriesMarker<Time>[]);
      });
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time) || String(a.time).localeCompare(String(b.time)));
    if (markers.length) createSeriesMarkers(candle, markers);

    // Open on the selected range (every cross in it on screen); older history is there when zooming out.
    const vi = visibleFrom == null ? -1 : data.findIndex((b) => b.time === visibleFrom);
    if (vi > 0) chart.timeScale().setVisibleLogicalRange({ from: vi, to: data.length - 1 + 4 });
    else chart.timeScale().fitContent();

    const last = data[data.length - 1];
    const prev = data[data.length - 2];
    const legendFor = (b: (typeof data)[number] | undefined, pb?: (typeof data)[number]): Legend => b ? {
      o: b.open, h: b.high, l: b.low, c: b.close, v: b.volume, chg: pb ? (b.close / pb.close - 1) * 100 : undefined,
      vals: Object.fromEntries([...MA_KEYS, "rsi", "rsi_short", "rsi_long", "rsi_sig", "rsi_short_sig", "rsi_long_sig", "macd", "macd_signal", "macd_hist",
        "stoch_k", "stoch_d", "stoch_short_k", "stoch_short_d", "stoch_medium_k", "stoch_medium_d", "stoch_long_k", "stoch_long_d", "atr", "adx", "plus_di", "minus_di", "obv"]
        .map((k) => [k, (b[k] as number | null) ?? null])),
    } : { vals: {} };
    setLegend(legendFor(last, prev));
    const idx = new Map(data.map((b, i) => [String(b.t), i]));
    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setLegend(legendFor(last, prev));
        return;
      }
      const i = idx.get(String(param.time));
      if (i !== undefined) setLegend(legendFor(data[i], data[i - 1]));
    });

    // ---- volume profile overlay (horizontal histogram on the right edge of the price pane)
    const draw = () => {
      // Top offset of every pane (1px separators) so each lower pane can carry its own label row.
      const tops: number[] = [];
      let acc = 0;
      for (const p of chart.panes()) { tops.push(acc); acc += p.getHeight() + 1; }
      setPaneTops((prev) => (prev.length === tops.length && prev.every((v, i) => v === tops[i]) ? prev : tops));
      const cv = overlay.current;
      if (!cv) return;
      const ctx = cv.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = el.clientWidth, h = el.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr; cv.style.width = `${w}px`; cv.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      if (!enabled.volume_profile || !volumeProfile) return;
      const plotW = chart.timeScale().width();
      const paneH = chart.panes()[0]?.getHeight() ?? h;
      const maxV = Math.max(...volumeProfile.bins.map((b) => b.volume));
      const maxW = Math.min(140, plotW * 0.22);
      const va = withAlpha(cssVar("--s1"), 0.28), nva = withAlpha(cssVar("--muted-foreground"), 0.16), poc = withAlpha(cssVar("--s4"), 0.55);
      for (const b of volumeProfile.bins) {
        const y1 = candle.priceToCoordinate(b.price_high), y2 = candle.priceToCoordinate(b.price_low);
        if (y1 === null || y2 === null) continue;
        const top = Math.max(0, Math.min(y1, y2)), bot = Math.min(paneH, Math.max(y1, y2));
        if (bot <= 0 || top >= paneH) continue;
        const bw = (b.volume / maxV) * maxW;
        ctx.fillStyle = Math.abs(b.price - volumeProfile.poc) < 1e-9 ? poc : b.in_value_area ? va : nva;
        ctx.fillRect(plotW - bw, top + 1, bw, Math.max(1, bot - top - 2));  // 2px surface gap between bins
      }
    };
    draw();
    const redraw = () => requestAnimationFrame(draw);
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
    const ro = new ResizeObserver(redraw);
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, JSON.stringify(enabled), levels, volumeProfile, patterns, cross?.[0], cross?.[1], resolvedTheme, currency, height, intraday,
    oscMode, stochKey, rsiKey, visibleFrom, trendlines, peak?.price, peak?.pct]);

  const legendItems = MA_KEYS.filter((k) => enabled[k] || (cross && (k === `sma${cross[0]}` || k === `sma${cross[1]}`)));
  const chg = legend.chg;
  return (
    <div className="relative" style={{ height }}>
      <div ref={wrap} className="absolute inset-0" />
      <canvas ref={overlay} className="pointer-events-none absolute inset-0" aria-hidden />
      <div className="pointer-events-none absolute left-2 top-1 z-10 flex max-w-[calc(100%-5rem)] flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] tabular">
        <span className="text-muted-foreground">시 <b className="font-medium text-foreground">{fmtPrice(legend.o, currency)}</b></span>
        <span className="text-muted-foreground">고 <b className="font-medium text-foreground">{fmtPrice(legend.h, currency)}</b></span>
        <span className="text-muted-foreground">저 <b className="font-medium text-foreground">{fmtPrice(legend.l, currency)}</b></span>
        <span className="text-muted-foreground">종 <b className="font-medium text-foreground">{fmtPrice(legend.c, currency)}</b></span>
        {chg !== undefined && <span className={chg >= 0 ? "text-up" : "text-down"}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span>}
        {legendItems.map((k) => (
          <span key={k} className="inline-flex items-center gap-1 text-muted-foreground">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: `var(${MA_COLORS[k]})` }} aria-hidden />
            {k.startsWith("sma") ? `${k.slice(3)}일선` : k === "ema20" ? "EMA 20" : "VWAP"} <b className="font-medium text-foreground">{fmtPrice(legend.vals[k], currency)}</b>
          </span>
        ))}
      </div>
      {subPanes.map((k, i) => paneTops[i + 1] === undefined ? null : (
        <div key={k} className="pointer-events-none absolute left-2 z-10 flex flex-wrap items-center gap-x-3 gap-y-0.5 rounded bg-card/70 px-1 text-[11px] tabular"
          style={{ top: paneTops[i + 1] + 2 }}>
          {k.startsWith("osc_") ? <OscLabel spec={oscSpec(oscMode, k.slice(4) as (typeof HZ)[number], stochSets, rsiPeriods)} vals={legend.vals} />
            : k === "rsi" ? <RsiLabel vals={legend.vals} periods={rsiPeriods} /> : <PaneLabel k={k} vals={legend.vals} />}
        </div>
      ))}
    </div>
  );
}

function zone(v: number | null | undefined) {
  if (typeof v !== "number") return null;
  return v >= 70 ? { t: "과매수", c: "text-down" } : v <= 30 ? { t: "과매도", c: "text-up" } : v >= 50 ? { t: "강세 구간", c: "text-muted-foreground" } : { t: "약세 구간", c: "text-muted-foreground" };
}

function RsiLabel({ vals, periods }: { vals: Record<string, number | null>; periods?: RsiPeriods }) {
  const p = periods ?? { short: 7, medium: 14, long: 28 };
  return (
    <>
      <span className="font-semibold text-foreground">RSI</span>
      {RSI_LINES.map((r) => {
        const v = vals[r.key];
        const z = zone(v);
        return (
          <span key={r.key} className="inline-flex items-center gap-1 text-muted-foreground">
            <span className="inline-block w-3.5 border-t-2" style={{ borderColor: `var(${r.color})`, borderStyle: r.style === LineStyle.Dashed ? "dashed" : "solid" }} aria-hidden />
            {r.label}({p[r.h]}) <b className="font-medium text-foreground">{num(v, 1)}</b>
            {z && <span className={z.c}>{z.t}</span>}
          </span>
        );
      })}
    </>
  );
}

function PaneLabel({ k, vals }: { k: string; vals: Record<string, number | null> }) {
  const items: [string, number | null, number][] =
    k === "macd" ? [["MACD", vals.macd, 2], ["시그널", vals.macd_signal, 2], ["히스토그램", vals.macd_hist, 2]]
      : k === "stoch" ? [["%K", vals.stoch_k, 1], ["%D", vals.stoch_d, 1]]
        : k === "adx" ? [["ADX", vals.adx, 1], ["+DI", vals.plus_di, 1], ["−DI", vals.minus_di, 1]]
          : k === "atr" ? [["ATR", vals.atr, 2]] : [["OBV", vals.obv, 0]];
  return (
    <>
      <span className="font-semibold text-foreground">{PANE_NAMES[k] ?? k}</span>
      {items.map(([l, v, d]) => <span key={l} className="text-muted-foreground">{l} <b className="font-medium text-foreground">{num(v, d)}</b></span>)}
    </>
  );
}

function OscLabel({ spec, vals }: { spec: ReturnType<typeof oscSpec>; vals: Record<string, number | null> }) {
  const k = vals[spec.k], d = vals[spec.d];
  const zone = typeof k !== "number" ? null : k >= spec.hi ? { t: "과매수", c: "text-down" } : k <= spec.lo ? { t: "과매도", c: "text-up" } : null;
  const turn = typeof k === "number" && typeof d === "number" ? (k > d ? "상승 흐름" : "하락 흐름") : null;
  return (
    <>
      <span className="font-semibold text-foreground">{spec.name}</span>
      <span className="inline-flex items-center gap-1 text-muted-foreground"><span className="inline-block h-0.5 w-3 bg-s8" aria-hidden />{spec.kLabel} <b className="font-medium text-foreground">{num(k, 1)}</b></span>
      <span className="inline-flex items-center gap-1 text-muted-foreground"><span className="inline-block h-0.5 w-3 bg-s1" aria-hidden />{spec.dLabel} <b className="font-medium text-foreground">{num(d, 1)}</b></span>
      {turn && <span className="text-muted-foreground">{turn}</span>}
      {zone && <span className={zone.c}>{zone.t}</span>}
    </>
  );
}
