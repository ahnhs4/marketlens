"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Legend, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer, Tooltip } from "recharts";

import { LineChart } from "@/components/charts/line-chart";
import { PageHeader, QueryState, Section, SignalBadge, SkeletonBlock, scoreTone } from "@/components/common";
import { api } from "@/lib/api";
import { compact, frac, isNum, num, price } from "@/lib/format";
import type { Scores } from "@/lib/types";

interface Item {
  symbol: string; name?: string; sector?: string; industry?: string; currency?: string; price?: number; status?: string;
  scores?: Scores; fundamentals?: Record<string, number | null>; performance?: Record<string, number | null>;
  volatility_1y?: number; max_drawdown_1y?: number; radar?: Record<string, number | null>;
  macro_empirical?: { market_beta: number; details: { factor: string; sensitivity: string }[] } | null;
  history?: { date: string; value: number }[];
}
const SERIES = ["--s1", "--s2", "--s3", "--s7", "--s5", "--s6"];
const DASH = ["", "6 3", "2 3", "8 3 2 3", "4 4", "1 3"];

function CompareInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const initial = sp.get("tickers") ?? "NVDA,AMD,AVGO,TSM";
  const [text, setText] = useState(initial.includes(",") ? initial : `${initial},AMD`);
  const tickers = (sp.get("tickers") ?? "NVDA,AMD,AVGO,TSM").split(",").filter(Boolean);
  const q = useQuery({ queryKey: ["compare", tickers.join(",")], enabled: tickers.length >= 2,
    queryFn: () => api<{ items: Item[] }>(`/api/compare?tickers=${encodeURIComponent(tickers.join(","))}`), staleTime: 300_000 });
  const cssVar = (n: string) => (typeof window === "undefined" ? "#888" : getComputedStyle(document.documentElement).getPropertyValue(n).trim());

  return (
    <div className="space-y-5">
      <PageHeader title="종목 비교" description="펀더멘털 · 기술적 지표 · 모멘텀 · 밸류에이션 · 위험 · 매크로 민감도를 나란히 비교 (최대 6종목)." />
      <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); router.push(`/compare?tickers=${encodeURIComponent(text.replace(/\s+/g, ""))}`); }}>
        <input value={text} onChange={(e) => setText(e.target.value.toUpperCase())} className="h-9 w-96 max-w-full rounded-md border bg-background px-3 font-mono text-sm" placeholder="NVDA,AMD,AVGO,TSM" aria-label="비교할 종목" />
        <button className="rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground">비교</button>
      </form>
      {tickers.length < 2 ? <p className="text-sm text-muted-foreground">쉼표로 구분해 종목을 2개 이상 입력하세요.</p> : (
        <QueryState q={q} label="비교" skeleton={<SkeletonBlock rows={10} />}>
          {(d) => {
            const ok = d.items.filter((i) => !i.status);
            const dims = Object.keys(ok[0]?.radar ?? {});
            const radarData = dims.map((dim) => ({ dim, ...Object.fromEntries(ok.map((i) => [i.symbol, i.radar?.[dim] ?? 0])) }));
            const rows: [string, (i: Item) => React.ReactNode][] = [
              ["주가", (i) => price(i.price, i.currency)],
              ["종합 점수", (i) => <b className={scoreTone(i.scores?.overall)}>{num(i.scores?.overall, 0)}</b>],
              ["기술적", (i) => <span className="inline-flex items-center gap-1.5">{num(i.scores?.technical, 0)}<SignalBadge signal={i.scores?.technical_signal} size="sm" /></span>],
              ["매크로", (i) => num(i.scores?.macro, 0)], ["펀더멘털", (i) => num(i.scores?.fundamental, 0)],
              ["단기 / 중기 / 장기", (i) => `${num(i.scores?.horizons.short.score, 0)} / ${num(i.scores?.horizons.medium.score, 0)} / ${num(i.scores?.horizons.long.score, 0)}`],
              ["1개월 / 3개월", (i) => `${isNum(i.performance?.["1m"]) ? i.performance!["1m"]!.toFixed(1) : "—"}% / ${isNum(i.performance?.["3m"]) ? i.performance!["3m"]!.toFixed(1) : "—"}%`],
              ["6개월 / 1년", (i) => `${isNum(i.performance?.["6m"]) ? i.performance!["6m"]!.toFixed(1) : "—"}% / ${isNum(i.performance?.["1y"]) ? i.performance!["1y"]!.toFixed(1) : "—"}%`],
              ["변동성 (1년)", (i) => frac(i.volatility_1y, 0, false)], ["최대 낙폭 (1년)", (i) => <span className="text-down">{frac(i.max_drawdown_1y)}</span>],
              ["시장 베타", (i) => num(i.macro_empirical?.market_beta, 2)],
              ["금리 민감도", (i) => i.macro_empirical?.details.find((x) => x.factor === "미 10년물 금리")?.sensitivity ?? "—"],
              ["시가총액", (i) => compact(i.fundamentals?.market_cap, i.currency)], ["PER · 선행 PER", (i) => `${num(i.fundamentals?.pe, 1)} · ${num(i.fundamentals?.forward_pe, 1)}`],
              ["EV/EBITDA", (i) => num(i.fundamentals?.ev_ebitda, 1)], ["매출 성장률", (i) => frac(i.fundamentals?.revenue_growth)],
              ["영업이익률", (i) => frac(i.fundamentals?.operating_margin)], ["ROE", (i) => frac(i.fundamentals?.roe)],
              ["부채비율 (D/E)", (i) => num(i.fundamentals?.debt_to_equity, 2)],
            ];
            return (
              <div className="space-y-4">
                {d.items.filter((i) => i.status).map((i) => <p key={i.symbol} className="text-sm text-warn">{i.symbol}: 데이터를 불러올 수 없습니다 (Data unavailable)</p>)}
                <div className="grid gap-4 xl:grid-cols-[480px_minmax(0,1fr)]">
                  <Section title="종목 프로필 레이더" subtitle="0~100점 · 밸류에이션은 높을수록 저렴 · 저위험 = 100 − 변동성">
                    <div className="h-96">
                      <ResponsiveContainer>
                        <RadarChart data={radarData} outerRadius="72%">
                          <PolarGrid stroke={cssVar("--border")} />
                          <PolarAngleAxis dataKey="dim" tick={{ fill: cssVar("--muted-foreground"), fontSize: 11 }} />
                          <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
                          {ok.map((i, k) => (
                            <Radar key={i.symbol} name={i.symbol} dataKey={i.symbol} stroke={cssVar(SERIES[k])} strokeWidth={2} strokeDasharray={DASH[k]} fill={cssVar(SERIES[k])} fillOpacity={0.06} />
                          ))}
                          <Tooltip contentStyle={{ background: cssVar("--popover"), border: `1px solid ${cssVar("--border")}`, fontSize: 12 }} formatter={(v) => (typeof v === "number" ? v.toFixed(0) : String(v))} />
                          <Legend wrapperStyle={{ fontSize: 12 }} />
                        </RadarChart>
                      </ResponsiveContainer>
                    </div>
                  </Section>
                  <Section title="스코어카드">
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs tabular">
                        <thead><tr className="border-b text-left"><th className="py-2 font-normal text-muted-foreground">지표</th>
                          {ok.map((i, k) => <th key={i.symbol} className="py-2 pr-3"><span className="inline-flex items-center gap-1.5"><span className="inline-block h-0.5 w-3" style={{ background: `var(${SERIES[k]})` }} /><a href={`/stock/${i.symbol}`} className="font-mono hover:underline">{i.symbol}</a></span><div className="max-w-36 truncate font-normal text-muted-foreground">{i.name}</div></th>)}</tr></thead>
                        <tbody>{rows.map(([label, f]) => (
                          <tr key={label} className="border-b last:border-0"><td className="py-1.5 text-muted-foreground">{label}</td>{ok.map((i) => <td key={i.symbol} className={cn("py-1.5 pr-3")}>{f(i)}</td>)}</tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </Section>
                </div>
                <Section title="상대 수익률 (1년, 시작=100)">
                  <LineChart height={280} lines={ok.filter((i) => i.history?.length).map((i, k) => ({ name: i.symbol, color: SERIES[k], points: i.history! }))} />
                </Section>
              </div>
            );
          }}
        </QueryState>
      )}
    </div>
  );
}

export default function ComparePage() {
  return <Suspense fallback={<SkeletonBlock rows={6} />}><CompareInner /></Suspense>;
}
