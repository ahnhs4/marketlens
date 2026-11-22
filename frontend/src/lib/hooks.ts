"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { STATIC, loadManifest } from "./static";
import type { Analogs, ChartData, NewsResponse, Overview, Regime, RsiPeriods, Scores, Snapshot, Technical } from "./types";

// Stock page: separate queries load progressively (quote -> chart -> technicals -> the rest),
// so a slow section (news, analogs) never blocks the chart.
export const useOverview = (t: string) =>
  useQuery({ queryKey: ["overview", t], queryFn: () => api<Overview>(`/api/stock/${encodeURIComponent(t)}`), refetchInterval: 30_000 });

export const useChart = (t: string, range: string, interval?: string, rsi: RsiPeriods = { short: 7, medium: 14, long: 28 },
  stoch?: Record<"short" | "medium" | "long", number[]>) => {
  const st = stoch ? (["short", "medium", "long"] as const).map((k) => stoch[k].join("-")).join(",") : "";
  return useQuery({
    queryKey: ["chart", t, range, interval ?? "", rsi.short, rsi.medium, rsi.long, st],
    queryFn: () =>
      api<ChartData>(`/api/stock/${encodeURIComponent(t)}/chart?range=${range}${interval ? `&interval=${interval}` : ""}` +
        `&rsi_period=${rsi.medium}&rsi_short=${rsi.short}&rsi_long=${rsi.long}${st ? `&stoch=${st}` : ""}`),
    staleTime: 60_000,
    refetchInterval: interval || range === "1D" ? 60_000 : false,
  });
};

export const useTechnical = (t: string, vp = "6M") =>
  useQuery({ queryKey: ["technical", t, vp], queryFn: () => api<Technical>(`/api/stock/${encodeURIComponent(t)}/technical?vp_period=${vp}`), staleTime: 120_000 });

export const useScores = (t: string) =>
  useQuery({ queryKey: ["scores", t], queryFn: () => api<Scores>(`/api/stock/${encodeURIComponent(t)}/scores`), staleTime: 180_000 });

export const useAnalogs = (t: string, years = 3) =>
  useQuery({ queryKey: ["similar", t, years], queryFn: () => api<Analogs>(`/api/stock/${encodeURIComponent(t)}/similar?years=${years}`), staleTime: 600_000 });

export const useStockNews = (t: string) =>
  useQuery({ queryKey: ["stocknews", t], queryFn: () => api<NewsResponse>(`/api/stock/${encodeURIComponent(t)}/news`), staleTime: 300_000 });

export const useSnapshot = () =>
  useQuery({ queryKey: ["snapshot"], queryFn: () => api<Snapshot>("/api/market/overview"), refetchInterval: 30_000 });

export const useRegime = () =>
  useQuery({ queryKey: ["regime"], queryFn: () => api<Regime>("/api/macro/regime"), staleTime: 300_000 });

export const useMarketNews = () =>
  useQuery({ queryKey: ["marketnews"], queryFn: () => api<NewsResponse>("/api/news/market"), staleTime: 300_000, refetchInterval: 600_000 });

/** Watchlist-based views: the app reads the user's own list; the public demo names the demo tickers explicitly (never the real watchlist). */
export function useScope() {
  const m = useQuery({ queryKey: ["manifest"], queryFn: loadManifest, staleTime: Infinity, enabled: STATIC });
  return { ready: !STATIC || !!m.data, qs: STATIC && m.data ? `?symbols=${m.data.tickers.map((t) => t.symbol).join(",")}` : "" };
}
