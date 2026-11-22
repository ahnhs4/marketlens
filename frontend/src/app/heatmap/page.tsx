"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import Link from "next/link";
import { type CSSProperties, useCallback, useMemo, useState } from "react";

import { Delta, PageHeader, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { compact, isNum, price } from "@/lib/format";
import { groupKo } from "@/lib/i18n";

// Market heatmap: tile area = market cap, colour = today's change. Click a tile to open the stock page.

interface Tile { symbol: string; name: string | null; group: string | null; cap_usd: number; change_pct: number | null; price: number | null; market: string; technical: number | null }
interface Heatmap {
  universe: string; name: string; tiles: Tile[]; members: number; cap_weighted_change: number | null; advancers: number; decliners: number;
  about?: string | null; note?: string | null; updated_at: string;
}
type Universe = "SP500" | "KOSPI";
interface Box { x: number; y: number; w: number; h: number }

/** Squarified treemap (Bruls et al.): `items` must be sorted by value, largest first. */
function squarify<T>(items: T[], value: (t: T) => number, box: Box): (Box & { item: T })[] {
  const out: (Box & { item: T })[] = [];
  const total = items.reduce((s, t) => s + value(t), 0);
  if (total <= 0 || box.w <= 0 || box.h <= 0) return out;
  const scale = (box.w * box.h) / total;
  let { x, y, w, h } = box;
  let i = 0;
  while (i < items.length) {
    const side = Math.min(w, h);
    let sum = 0, lo = Infinity, hi = 0, best = Infinity, j = i;
    while (j < items.length) {
      const a = value(items[j]) * scale, s = sum + a, mx = Math.max(hi, a), mn = Math.min(lo, a);
      const worst = Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
      if (worst > best) break;
      best = worst; sum = s; hi = mx; lo = mn; j++;
    }
    const thick = sum / side;
    let off = 0;
    for (let k = i; k < j; k++) {
      const len = (value(items[k]) * scale) / thick;
      out.push(w >= h ? { item: items[k], x, y: y + off, w: thick, h: len } : { item: items[k], x: x + off, y, w: len, h: thick });
      off += len;
    }
    if (w >= h) { x += thick; w -= thick; } else { y += thick; h -= thick; }
    i = j;
  }
  return out;
}

const FULL = 3; // a ±3% move gets the full colour
function tileColour(chg: number | null): CSSProperties {
  if (!isNum(chg)) return { backgroundColor: "var(--muted)" };
  const strength = Math.round(14 + Math.min(1, Math.abs(chg) / FULL) * 70);
  return { backgroundColor: `color-mix(in oklab, var(${chg >= 0 ? "--up-mark" : "--down-mark"}) ${Math.abs(chg) < 0.05 ? 6 : strength}%, var(--card))` };
}
const pctText = (v: number | null) => (isNum(v) ? `${v > 0 ? "+" : ""}${v.toFixed(2)}%` : "—");
const HEAD = 15; // sector label strip

function Treemap({ d }: { d: Heatmap }) {
  const [width, setWidth] = useState(0);
  // measure once on mount (so the first paint already has tiles), then follow resizes
  const ref = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    setWidth(Math.round(el.clientWidth));
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const height = width < 640 ? Math.round(width * 1.5) : Math.max(460, Math.min(780, Math.round(width * 0.5)));
  const kr = d.universe === "KOSPI";
  const layout = useMemo(() => {
    if (!width) return { groups: [], tiles: [] as (Box & { item: Tile })[] };
    const tiles = d.tiles.filter((t) => t.cap_usd > 0);
    const unknown = tiles.filter((t) => !t.group || t.group === "기타").length;
    // Sector labels are only trustworthy when most members have one (S&P 500 yes, KOSPI no): otherwise draw one flat map.
    if (!tiles.length || unknown / tiles.length > 0.2) return { groups: [], tiles: squarify(tiles, (t) => t.cap_usd, { x: 0, y: 0, w: width, h: height }) };
    const by = new Map<string, Tile[]>();
    for (const t of tiles) by.set(t.group ?? "기타", [...(by.get(t.group ?? "기타") ?? []), t]);
    const groups = [...by.entries()].map(([name, items]) => ({ name, items, cap: items.reduce((s, t) => s + t.cap_usd, 0) })).sort((a, b) => b.cap - a.cap);
    const boxes = squarify(groups, (g) => g.cap, { x: 0, y: 0, w: width, h: height });
    return {
      groups: boxes.map((b) => ({ ...b, name: b.item.name })),
      tiles: boxes.flatMap((b) => squarify(b.item.items, (t) => t.cap_usd, { x: b.x + 1, y: b.y + HEAD, w: Math.max(0, b.w - 2), h: Math.max(0, b.h - HEAD - 1) })),
    };
  }, [d.tiles, width, height]);

  return (
    <div ref={ref} className="relative w-full overflow-hidden rounded-lg bg-background" style={{ height: width ? height : 480 }}>
      {layout.groups.map((g) => (
        <div key={g.name} className="absolute overflow-hidden border border-background" style={{ left: g.x, top: g.y, width: g.w, height: g.h }}>
          {g.w > 44 && <div className="truncate px-1 text-[9.5px] font-medium uppercase leading-[15px] tracking-wide text-muted-foreground">{groupKo(g.name)}</div>}
        </div>
      ))}
      {layout.tiles.map(({ item: t, x, y, w, h }) => {
        const label = kr ? (t.name ?? t.symbol) : t.symbol;
        const big = w >= 46 && h >= 30, mid = w >= 30 && h >= 15;
        const size = Math.max(9, Math.min(19, Math.min(w / (kr ? Math.max(3, label.length) * 1.05 : Math.max(3, label.length) * 0.72), h / 2.6)));
        return (
          <Link key={t.symbol} href={`/stock/${encodeURIComponent(t.symbol)}`}
            title={`${t.name ?? t.symbol} (${t.symbol})\n${pctText(t.change_pct)} · ${price(t.price, t.market === "KR" ? "KRW" : "USD")}\n시가총액 ${compact(t.cap_usd, "USD")}${t.group ? ` · ${groupKo(t.group)}` : ""}`}
            className="absolute flex flex-col items-center justify-center overflow-hidden border border-background text-center leading-tight text-foreground transition-[filter] hover:z-10 hover:brightness-125 hover:outline hover:outline-1 hover:outline-foreground/70"
            style={{ left: x, top: y, width: w, height: h, ...tileColour(t.change_pct) }}>
            {mid && <span className={cn("max-w-full truncate px-0.5 font-semibold", !kr && "font-mono")} style={{ fontSize: big ? size : 9 }}>{label}</span>}
            {big && <span className="tabular opacity-85" style={{ fontSize: Math.max(8.5, size * 0.68) }}>{pctText(t.change_pct)}</span>}
          </Link>
        );
      })}
    </div>
  );
}

export default function HeatmapPage() {
  const [universe, setUniverse] = useState<Universe>("SP500");
  const q = useQuery({ queryKey: ["heatmap", universe], queryFn: () => api<Heatmap>(`/api/market/heatmap?universe=${universe}`), staleTime: 60_000 });
  const d = q.data;
  return (
    <div className="space-y-5">
      <PageHeader title="히트맵" description="타일 크기는 시가총액, 색은 당일 등락률입니다. 타일을 누르면 종목 페이지로 이동합니다." right={<UpdatedAt at={d?.updated_at} />} />
      <Section
        title={<span className="flex flex-wrap items-baseline gap-x-4 gap-y-1">{universe === "SP500" ? "S&P 500" : "코스피"}
          {d && (
            <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-normal tabular">
              <span>시총가중 <Delta value={d.cap_weighted_change} showIcon={false} className="font-semibold" /></span>
              <span><b className="text-up">{d.advancers}</b> 상승 · <b className="text-down">{d.decliners}</b> 하락</span>
              <span className="text-muted-foreground">{d.tiles.length}종목</span>
            </span>
          )}</span>}
        right={<Segmented size="sm" ariaLabel="지수 선택" value={universe} onChange={setUniverse} options={[{ value: "SP500", label: "S&P 500" }, { value: "KOSPI", label: "코스피" }]} />}
        bodyClassName="p-3">
        <QueryState q={q} label="히트맵" skeleton={<SkeletonBlock rows={12} />}>
          {(d) => d.tiles.length === 0 ? <p className="p-2 text-sm text-muted-foreground">{d.note ?? "스캔 결과가 아직 없습니다 (Data unavailable)"}</p> : (
            <>
              <Treemap d={d} />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-1 text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1.5 tabular">
                  -{FULL}%{[-3, -2, -1, 0, 1, 2, 3].map((v) => <i key={v} className="h-3 w-6 rounded-[3px]" style={tileColour(v)} />)}+{FULL}%
                </span>
                <span className="max-w-3xl leading-relaxed">{d.about ?? d.note}</span>
              </div>
            </>
          )}
        </QueryState>
      </Section>
    </div>
  );
}
