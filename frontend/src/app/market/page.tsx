"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import Link from "next/link";
import { useState } from "react";

import { PageHeader, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { FearGreedPanel } from "@/components/fear-greed";
import { SnapshotCards } from "@/components/market";
import { api } from "@/lib/api";
import { isNum, pct } from "@/lib/format";
import { useSnapshot } from "@/lib/hooks";
import { breadthKo, scanStateKo } from "@/lib/i18n";

interface SectorRow { symbol: string; name: string; price?: number; perf?: Record<string, number>; rs_3m_vs_spy?: number | null; above_sma50?: boolean; status?: string; as_of?: string }
interface Breadth { members: number; pct_above_sma50: number; pct_above_sma200: number; new_highs: number; new_lows: number; advancers: number; decliners: number; ad_ratio: number; score: number; label: string; formula: string; as_of: string }

function DivergingBars({ rows, period }: { rows: SectorRow[]; period: string }) {
  const valid = rows.filter((r) => r.perf && isNum(r.perf[period])).sort((a, b) => b.perf![period] - a.perf![period]);
  const max = Math.max(1, ...valid.map((r) => Math.abs(r.perf![period])));
  return (
    <ul className="space-y-1">
      {valid.map((r) => {
        const v = r.perf![period];
        const w = (Math.abs(v) / max) * 50;
        return (
          <li key={r.symbol} className="grid grid-cols-[150px_1fr_64px] items-center gap-2 text-xs" title={`${r.name} (${r.symbol}) ${pct(v)}`}>
            <span className="truncate"><Link href={`/stock/${r.symbol}`} className="hover:underline">{r.name}</Link> <span className="text-muted-foreground">{r.symbol}</span></span>
            <div className="relative h-3">
              <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
              <div className={cn("absolute inset-y-0.5 rounded-sm", v >= 0 ? "left-1/2 bg-up-mark" : "right-1/2 bg-down-mark")} style={{ width: `${w}%` }} />
            </div>
            <span className={cn("text-right tabular", v >= 0 ? "text-up" : "text-down")}>{pct(v)}</span>
          </li>
        );
      })}
    </ul>
  );
}

function BreadthBlock({ name, b }: { name: string; b: Breadth }) {
  const tone = b.label === "Healthy" ? "text-up" : b.label === "Weak" ? "text-down" : "";
  return (
    <div className="rounded-lg border p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">{name}</span>
        <span className={cn("text-xl font-semibold tabular", tone)}>{b.score.toFixed(0)} <span className="text-xs font-normal text-muted-foreground">/100 · {breadthKo(b.label)}</span></span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs tabular">
        <dt className="text-muted-foreground">50일선 위 종목 비율</dt><dd className="text-right">{b.pct_above_sma50.toFixed(1)}%</dd>
        <dt className="text-muted-foreground">200일선 위 종목 비율</dt><dd className="text-right">{b.pct_above_sma200.toFixed(1)}%</dd>
        <dt className="text-muted-foreground">52주 신고가 / 신저가</dt><dd className="text-right"><span className="text-up">{b.new_highs}</span> / <span className="text-down">{b.new_lows}</span></dd>
        <dt className="text-muted-foreground">상승 / 하락 종목 수</dt><dd className="text-right"><span className="text-up">{b.advancers}</span> / <span className="text-down">{b.decliners}</span></dd>
      </dl>
      <p className="mt-2 text-[10px] text-muted-foreground">구성 {b.members}종목 · {b.as_of} 기준 · {b.formula}</p>
    </div>
  );
}

function CorrHeatmap({ assets, matrix }: { assets: string[]; matrix: (number | null)[][] }) {
  const cell = (v: number | null) => {
    if (v === null) return { background: "transparent" };
    const a = Math.min(1, Math.abs(v));
    // Diverging: blue (positive) <-> red (negative), neutral gray midpoint
    const c = v >= 0 ? "57,135,229" : "230,103,103";
    return { background: `rgba(${c},${0.08 + a * 0.75})` };
  };
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] tabular">
        <thead><tr><th />{assets.map((a) => <th key={a} className="h-24 w-12 align-bottom font-normal text-muted-foreground"><div className="w-4 -rotate-60 origin-bottom-left translate-x-5 whitespace-nowrap">{a}</div></th>)}</tr></thead>
        <tbody>
          {assets.map((a, i) => (
            <tr key={a}>
              <th className="whitespace-nowrap pr-2 text-right font-normal text-muted-foreground">{a}</th>
              {matrix[i].map((v, j) => (
                <td key={j} className="size-12 border border-card text-center" style={cell(v)} title={`${a} vs ${assets[j]}: ${v === null ? "없음" : v.toFixed(2)}`}>
                  {v === null ? "—" : v.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-[10px] text-muted-foreground">파랑 = 같은 방향으로 움직임, 빨강 = 반대 방향, 옅은 색 = 무관. 모든 칸에 수치를 함께 표시합니다.</p>
    </div>
  );
}

export default function MarketPage() {
  const snap = useSnapshot();
  const sectors = useQuery({ queryKey: ["sectors"], queryFn: () => api<{ sectors: SectorRow[]; updated_at: string; source: string }>("/api/market/sectors"), staleTime: 300_000 });
  const breadth = useQuery({ queryKey: ["breadth"], queryFn: () => api<{ breadth: Record<string, Breadth>; scan: { state: string } }>("/api/market/breadth"), staleTime: 120_000 });
  const [period, setPeriod] = useState("1d");
  const [win, setWin] = useState("90d");
  const corr = useQuery({ queryKey: ["corr", win], queryFn: () => api<{ assets: string[]; matrix: (number | null)[][]; observations: number; updated_at: string; note: string; status?: string }>(`/api/market/correlation?window=${win}`), staleTime: 1_800_000 });
  return (
    <div className="space-y-5">
      <PageHeader title="시장 개요" description="글로벌 지수, 섹터 순환, 시장 폭(브레드스), 자산 간 상관관계." />
      <Section title="주요 지수" right={<UpdatedAt at={snap.data?.updated_at} source="Yahoo Finance" />} bodyClassName="p-3">
        {snap.isError ? <QueryState q={snap} label="지수">{() => null}</QueryState> : <SnapshotCards cards={snap.data?.cards} loading={snap.isLoading} />}
      </Section>
      <FearGreedPanel />
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="섹터 · 산업별 수익률" subtitle="미국 섹터/산업 ETF를 대용 지표로 사용"
          right={<Segmented size="xs" value={period} onChange={setPeriod} options={[["1d", "1일"], ["1w", "1주"], ["1m", "1개월"], ["3m", "3개월"], ["ytd", "연초 대비"]].map(([value, label]) => ({ value, label }))} />}>
          <QueryState q={sectors} label="섹터" skeleton={<SkeletonBlock rows={12} />}>
            {(s) => <DivergingBars rows={s.sectors} period={period} />}
          </QueryState>
        </Section>
        <Section title="시장 폭 (브레드스)" subtitle="최신 스캔의 지수 구성종목 전체 일봉으로 계산">
          <QueryState q={breadth} label="시장 폭" skeleton={<SkeletonBlock rows={6} />}>
            {(b) => Object.keys(b.breadth).length === 0 ? <p className="text-sm text-muted-foreground">첫 유니버스 스캔이 끝나면 표시됩니다 ({scanStateKo(b.scan.state)}).</p> : (
              <div className="grid gap-3 sm:grid-cols-2">
                {b.breadth.SP500 && <BreadthBlock name="S&P 500" b={b.breadth.SP500} />}
                {b.breadth.KOSPI && <BreadthBlock name="코스피 (시총 상위 100)" b={b.breadth.KOSPI} />}
              </div>
            )}
          </QueryState>
        </Section>
      </div>
      <Section title="자산 간 상관관계" subtitle="일간 수익률 기준 (미 10년물 금리는 일간 변화폭)"
        right={<Segmented size="xs" value={win} onChange={setWin} options={[["30d", "30일"], ["90d", "90일"], ["1y", "1년"], ["3y", "3년"]].map(([value, label]) => ({ value, label }))} />}>
        <QueryState q={corr} label="상관관계" skeleton={<SkeletonBlock rows={8} />}>
          {(c) => c.status ? <p className="text-sm text-muted-foreground">{c.status}</p> : <><CorrHeatmap assets={c.assets} matrix={c.matrix} /><p className="mt-1 text-[10px] text-muted-foreground">관측치 {c.observations}개 · {c.note}</p></>}
        </QueryState>
      </Section>
    </div>
  );
}
