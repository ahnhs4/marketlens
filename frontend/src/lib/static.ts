// Static demo build (NEXT_PUBLIC_STATIC=1): the same UI, served from GitHub Pages with every API response
// read from a frozen snapshot (public/snapshot/<key>.json, written by portfolio-site/build_app_snapshot.py).
// Nothing personal is in the snapshot: portfolio, watchlist, trading, alerts and settings pages show a notice.

export const STATIC = process.env.NEXT_PUBLIC_STATIC === "1";
export const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
export const PRIVATE_ROUTES = ["/watchlist", "/portfolio", "/trading", "/alerts", "/settings", "/journal"];

/** FNV-1a 32-bit of the exact request string (path + query [+ "|" + body]); the Python exporter computes the same. */
export function snapKey(path: string, body?: string | null): string {
  const s = body ? `${path}|${body}` : path;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

export interface Manifest { as_of: string; tickers: { symbol: string; name: string; name_ko?: string | null; market: string; exchange?: string | null }[]; profile_url: string }

let manifest: Promise<Manifest> | null = null;
export function loadManifest(): Promise<Manifest> {
  manifest ??= fetch(`${BASE}/snapshot/manifest.json`).then((r) => r.json());
  return manifest;
}

const RANGE_DAYS: Record<string, number> = { "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "3Y": 1096, "5Y": 1827, MAX: 100000 };

export interface Fallback {
  path: string;
  adapt?: (body: unknown) => unknown;
  /** Snapshot of what the backend computed for the requested view itself (range peak, trendlines), laid over the reused answer. */
  view?: string;
}

/** Requests that have no snapshot of their own but can be answered from one that does. */
export function fallbacks(path: string): Fallback[] {
  const [p, q = ""] = path.split("?");
  const params = new URLSearchParams(q);
  const out: Fallback[] = [];
  if (/^\/api\/stock\/[^/]+\/chart$/.test(p)) {
    const range = params.get("range") ?? "1Y";
    if (params.get("interval")) return out;   // minute bars are not in the demo
    // daily ranges share the 3Y bars, 5Y the weekly MAX bars (build_app_snapshot.py)
    const base = ["1M", "3M", "6M", "1Y"].includes(range) ? "3Y" : range === "5Y" ? "MAX" : range;
    if (!params.get("stoch")) params.set("stoch", "5-3-3,10-6-6,20-12-12");   // first render, before settings arrive
    const view = range === base ? undefined : `${p}?${decodeURIComponent(params.toString())}#view`;
    params.set("range", base);
    const days = RANGE_DAYS[range] ?? 366;
    out.push({
      view,
      path: `${p}?${decodeURIComponent(params.toString())}`,
      adapt: (body) => {
        const b = body as { bars?: { time: string | number }[]; visible_from?: string | null; range?: string };
        if (!b?.bars?.length || range === base) return b;
        const last = new Date(String(b.bars[b.bars.length - 1].time));
        const from = new Date(last.getTime() - days * 86400_000).toISOString().slice(0, 10);
        const first = b.bars.find((x) => String(x.time) >= from);
        return { ...b, range, visible_from: first ? String(first.time) : String(b.bars[0].time) };
      },
    });
  } else if (/^\/api\/stock\/[^/]+\/technical$/.test(p) && params.get("vp_period") !== "6M") {
    out.push({ path: `${p}?vp_period=6M` });
  } else if (/^\/api\/stock\/[^/]+\/similar$/.test(p) && params.get("years") !== "3") {
    out.push({ path: `${p}?years=3` });
  } else if (p === "/api/recommendations/combined" && params.get("w_tech") !== "0.6") {
    params.set("w_tech", "0.6");
    params.set("w_macro", "0.40");
    out.push({ path: `${p}?${decodeURIComponent(params.toString())}` });
  }
  return out;
}

/** Lay a range's own snapshot (Fallback.view) over the shared bars, starting them where that range's answer starts. */
export function withView(data: unknown, view: { bars_from?: string | number | null } & Record<string, unknown>): unknown {
  const { bars_from, ...rest } = view;
  const d = data as { bars?: { time: string | number }[] };
  const bars = bars_from == null || !d.bars ? d.bars : d.bars.filter((b) => String(b.time) >= String(bars_from));
  return { ...d, ...rest, bars };
}

/** POST bodies keyed without fields that change by the day (a backtest's end date defaults to today). */
export function snapBody(path: string, body: string | null): string | null {
  if ((path !== "/api/backtest/run" && path !== "/api/builder/run") || !body) return body;
  try {
    const o = JSON.parse(body) as Record<string, unknown>;
    delete o.end;
    if (path === "/api/builder/run") delete o.name;
    return JSON.stringify(o);
  } catch {
    return body;
  }
}

/** Navigate to a stock page; in the demo, say so instead when the ticker has no snapshot. */
export async function openStock(push: (href: string) => void, symbol: string, notify: (title: string, body: string) => void) {
  if (STATIC) {
    const m = await loadManifest();
    if (!m.tickers.some((t) => t.symbol.toUpperCase() === symbol.toUpperCase())) {
      notify("데모에 없는 종목입니다", `${symbol} - 데모에는 대표 종목 ${m.tickers.length}개만 저장돼 있습니다. 실제 앱에서는 모든 종목을 분석합니다.`);
      return;
    }
  }
  push(`/stock/${encodeURIComponent(symbol)}`);
}
