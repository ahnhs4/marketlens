"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellPlus, Columns3, FlaskConical, Star } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Delta, Freshness, SignalBadge, SkeletonBlock, Unavailable } from "@/components/common";
import { api, post } from "@/lib/api";
import { compact, currencySymbol, dateKo, isNum, price, timeKST } from "@/lib/format";
import { groupKo, ratingKo, sessionKo } from "@/lib/i18n";
import type { Overview, Scores } from "@/lib/types";

export function StockHeader({ q, scores }: { q: { data?: Overview; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void }; scores?: Scores }) {
  const qc = useQueryClient();
  const [now] = useState(() => Date.now());
  const { data: wls } = useQuery({ queryKey: ["watchlists"], queryFn: () => api<{ id: number; name: string; symbols: string[] }[]>("/api/watchlists") });
  const add = useMutation({
    mutationFn: ({ wid, symbol }: { wid: number; symbol: string }) => post(`/api/watchlists/${wid}/items`, { symbol }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["watchlists"] }); toast.success("관심종목에 추가했습니다"); },
    onError: (e: Error) => toast.error(e.message),
  });
  if (q.isLoading) return <div className="rounded-xl border bg-card p-4"><SkeletonBlock rows={3} /></div>;
  if (q.isError || !q.data) return <Unavailable message={(q.error as Error)?.message} onRetry={q.refetch} label="시세" />;
  const { quote: qt, profile: p, market_status: ms } = q.data;
  const f = p.fundamentals ?? {};
  const inList = wls?.some((w) => w.symbols.includes(q.data!.symbol));
  const ext = qt.market_state && ["PRE", "PREPRE"].includes(qt.market_state) ? qt.pre_market_price : qt.market_state && ["POST", "POSTPOST"].includes(qt.market_state) ? qt.post_market_price : null;
  const earn = p.earnings_timestamp ? new Date(p.earnings_timestamp * 1000) : null;
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-2xl font-semibold tracking-tight">{q.data.symbol}</h1>
            {scores && <SignalBadge signal={scores.technical_signal} size="md" />}
            {scores?.rating && <span className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground">종합: <b className="text-foreground">{ratingKo(scores.rating)}</b></span>}
          </div>
          <p className="mt-0.5 truncate text-sm text-muted-foreground">
            {p.name_ko && p.name_ko !== p.name ? <span className="text-foreground">{p.name_ko} · </span> : null}{p.name}
            {p.exchange ? ` · ${p.exchange}` : ""}{p.sector ? ` · ${groupKo(p.sector)}` : ""}{p.industry ? ` / ${p.industry}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {wls && wls.length > 0 && !inList && (
            <button className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:border-gold/35 hover:bg-accent" onClick={() => add.mutate({ wid: wls[0].id, symbol: q.data!.symbol })}>
              <Star className="size-3.5" /> “{wls[0].name}”에 추가
            </button>
          )}
          {inList && <span className="inline-flex items-center gap-1.5 rounded-full border border-gold/40 bg-gold/[0.08] px-3 py-1 text-xs text-gold"><Star className="size-3.5 fill-current" /> 관심종목</span>}
          <Link href={`/alerts?symbol=${q.data.symbol}`} className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:border-gold/35 hover:bg-accent"><BellPlus className="size-3.5" />알림 설정</Link>
          <Link href={`/backtest?ticker=${q.data.symbol}`} className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:border-gold/35 hover:bg-accent"><FlaskConical className="size-3.5" />백테스트</Link>
          <Link href={`/compare?tickers=${q.data.symbol}`} className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors hover:border-gold/35 hover:bg-accent"><Columns3 className="size-3.5" />비교</Link>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="font-display text-[44px] leading-none tracking-tight tabular">{currencySymbol(qt.currency)}{price(qt.price, qt.currency)}</span>
            <Delta value={qt.change_pct} className="text-base font-medium" />
            <span className={`text-sm tabular ${isNum(qt.change) && qt.change > 0 ? "text-up" : isNum(qt.change) && qt.change < 0 ? "text-down" : "text-muted-foreground"}`}>{isNum(qt.change) ? `${qt.change > 0 ? "+" : ""}${price(qt.change, qt.currency)}` : ""}</span>
          </div>
          {ext && isNum(ext) && <div className="text-xs text-muted-foreground">시간외: {price(ext, qt.currency)}</div>}
          <div className="mt-1"><Freshness delay={qt.delay_minutes} quoteTime={qt.quote_time} source={qt.source} /></div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-4 lg:grid-cols-7">
          {[
            ["시가총액", compact(f.market_cap as number, qt.currency)],
            ["당일 범위", `${price(qt.low, qt.currency)} – ${price(qt.high, qt.currency)}`],
            ["52주 범위", `${price(qt.fifty_two_week_low, qt.currency)} – ${price(qt.fifty_two_week_high, qt.currency)}`],
            ["PER(최근 12개월)", isNum(f.pe) ? (f.pe as number).toFixed(1) : "—"],
            ["선행 PER", isNum(f.forward_pe) ? (f.forward_pe as number).toFixed(1) : "—"],
            [earn && earn.getTime() < now ? "최근 실적 발표" : "다음 실적 발표", earn ? dateKo(earn) : "—"],
            ["장 상태", ms ? `${ms.market === "US" ? "미국" : "한국"} ${sessionKo(ms.state)}` : "—"],
          ].map(([k, v]) => (
            <div key={k}><dt className="text-muted-foreground">{k}</dt><dd className="font-medium tabular">{v}</dd></div>
          ))}
        </dl>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">시세 시각 {timeKST(qt.quote_time)} · 가격 출처 {qt.source}</p>
    </div>
  );
}
