"use client";

import { cn } from "cn";
import { SlidersHorizontal } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AnalystPanel } from "@/components/analyst-panel";
import { findCrosses, OSC_H, type OscMode, PriceChart } from "@/components/charts/price-chart";
import { Pill, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { NewsRow, SummaryBlock } from "@/components/news";
import { useSettings } from "@/components/providers";
import { DartPanel } from "@/components/stock/dart-panel";
import { BullBear, FundamentalsPanel } from "@/components/stock/fundamentals";
import { StockHeader } from "@/components/stock/header";
import { OutlookPanel } from "@/components/stock/outlook";
import { AnalogsPanel, HorizonPanel, LevelsPanel, PatternsPanel, ScoreStrip, SignalPanel, TrendPanel } from "@/components/stock/panels";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { useAnalogs, useChart, useOverview, useScores, useStockNews, useTechnical } from "@/lib/hooks";
import { price } from "@/lib/format";
import { filingKo } from "@/lib/i18n";
import type { Bar, ChartData } from "@/lib/types";

const RANGES: { value: string; label: string }[] = [
  { value: "1D", label: "1일" }, { value: "1W", label: "1주" }, { value: "1M", label: "1개월" }, { value: "3M", label: "3개월" },
  { value: "6M", label: "6개월" }, { value: "1Y", label: "1년" }, { value: "3Y", label: "3년" }, { value: "5Y", label: "5년" },
  { value: "MAX", label: "전체" },
];
const INTERVALS: { value: string; label: string }[] = [
  { value: "", label: "일봉" }, { value: "1m", label: "1분" }, { value: "5m", label: "5분" }, { value: "15m", label: "15분" }, { value: "1h", label: "1시간" },
];
const IND_LABELS: Record<string, string> = {
  osc3: "단기 · 중기 · 장기 하단지표 (3칸)",
  sma5: "5일선", sma20: "20일선", sma50: "50일선", sma60: "60일선", sma120: "120일선", sma200: "200일선", ema20: "EMA 20",
  trendlines: "추세선 (지금 지켜지는 선)",
  drawdown: "고점 대비 하락률 (고점 선)",
  bb: "볼린저 밴드", vwap: "VWAP", volume: "거래량", volume_profile: "매물대(볼륨 프로파일)", levels: "지지 / 저항선",
  rsi: "RSI", macd: "MACD", stoch: "스토캐스틱", atr: "ATR", adx: "ADX / DMI", obv: "OBV",
};
const CROSS_PAIRS: { value: string; label: string; pair: [number, number] | null }[] = [
  { value: "5-20", label: "단기 5·20일", pair: [5, 20] },
  { value: "20-60", label: "중기 20·60일", pair: [20, 60] },
  { value: "50-200", label: "장기 50·200일", pair: [50, 200] },
  { value: "off", label: "끄기", pair: null },
];

/** `from` = index of the selected range's first bar: the most recent crosses may predate it, the counts don't. */
/** How far the price is below the highs: the selected range, 52 weeks, and everything loaded (merged when they are the same high). */
function DrawdownStrip({ dd, currency }: { dd: NonNullable<ChartData["drawdown"]>; currency?: string | null }) {
  return (
    <div className="mx-2 mb-2 flex flex-wrap items-stretch gap-2">
      {dd.peaks.map((p) => {
        const deep = (p.pct ?? 0) <= -20;
        return (
          <div key={p.key} className="min-w-[168px] flex-1 rounded-lg border bg-foreground/[0.02] px-3 py-2"
            title={`${p.date} 고점 이후 최저 ${price(p.trough, currency)} (${p.trough_pct?.toFixed(1)}%), 그 저점에서 ${p.rebound_pct !== null && p.rebound_pct >= 0 ? "+" : ""}${p.rebound_pct?.toFixed(1)}% 반등`}>
            <div className="text-[11px] text-muted-foreground">{p.label} 대비</div>
            <div className="flex items-baseline gap-2">
              <span className={cn("text-lg font-semibold tabular", (p.pct ?? 0) < -0.05 ? (deep ? "text-down" : "text-foreground") : "text-up")}>
                {p.pct === null ? "—" : Math.abs(p.pct) < 0.05 ? "고점" : `${p.pct.toFixed(1)}%`}
              </span>
              <span className="text-[11px] text-muted-foreground tabular">{price(p.peak, currency)} · {p.date.slice(2, 16).replace(/-/g, ".")}{p.days > 0 ? ` · ${p.days}일 전` : ""}</span>
            </div>
            {p.trough_pct !== null && p.pct !== null && p.trough_pct < p.pct - 0.5 && (
              <div className="text-[10.5px] text-muted-foreground tabular">고점 후 최대 {p.trough_pct.toFixed(1)}%까지 빠졌다가 {p.rebound_pct !== null && p.rebound_pct >= 0 ? "+" : ""}{p.rebound_pct?.toFixed(1)}% 반등</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** The trendlines drawn on the chart, most credible first, and the shape they make together. */
function TrendlineSummary({ c, currency }: { c: ChartData; currency?: string | null }) {
  const tls = c.trendlines ?? [];
  if (!tls.length) return <p className="px-2 pt-2 text-[11px] text-muted-foreground">추세선: 지금 지켜지고 있는 추세선이 없습니다 (가격 근처에서 2번 이상 닿고 깨지지 않은 선 없음).</p>;
  return (
    <div className="mx-2 mt-2 rounded-lg border bg-foreground/[0.02] px-3 py-2 text-xs">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="font-medium">추세선</span>
        {c.trend_pattern && <Pill tone="primary">{c.trend_pattern.name} · {c.trend_pattern.bias}</Pill>}
        <span className="text-[11px] text-muted-foreground">신뢰도 = 터치 횟수 · 기간 · 최근 터치 · 이탈 여부 · 현재가와의 거리 · 실선 = 3번 이상 확정, 점선 = 2번 (잠정)</span>
      </div>
      <ul className="grid gap-1 md:grid-cols-2">
        {tls.map((t, i) => (
          <li key={i} className="flex items-center gap-2">
            <span className={cn("h-0.5 w-5 shrink-0 rounded", t.confirmed ? "bg-foreground dark:bg-white" : "opacity-70")}
              style={t.confirmed ? undefined : { backgroundImage: "repeating-linear-gradient(90deg, var(--foreground) 0 3px, transparent 3px 6px)" }} />
            <b className={t.kind === "support" ? "text-up" : "text-down"}>{t.name}</b>
            <span className="tabular text-muted-foreground">신뢰도 {t.score.toFixed(0)} · {t.touches}회 터치{t.confirmed ? "" : " (잠정)"} · 현재 선 {price(t.end.value, currency)} ({t.distance_pct >= 0 ? "+" : ""}{t.distance_pct.toFixed(1)}%)</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CrossSummary({ bars, pair, from = 0, rangeLabel }: { bars: Bar[]; pair: [number, number]; from?: number; rangeLabel?: string }) {
  const crosses = findCrosses(bars, pair[0], pair[1]);
  const inRange = crosses.filter((c) => c.index >= from);
  const last = bars[bars.length - 1];
  const f = last?.[`sma${pair[0]}`], sl = last?.[`sma${pair[1]}`];
  const lastG = [...crosses].reverse().find((c) => c.kind === "golden");
  const lastD = [...crosses].reverse().find((c) => c.kind === "death");
  const ago = (c?: { index: number }) => (c ? `${bars.length - 1 - c.index}봉 전` : "");
  const when = (c?: { time: string | number }) => (c ? (typeof c.time === "number" ? new Date(c.time * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : c.time) : "불러온 기간 내 없음");
  const above = typeof f === "number" && typeof sl === "number" ? f > sl : null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-2 pb-1 text-xs">
      {above === null ? <Pill>이동평균 계산에 필요한 데이터 부족</Pill> : (
        <span className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-semibold",
          above ? "border-up/40 bg-up/10 text-up" : "border-down/40 bg-down/10 text-down")}>
          현재: {pair[0]}일선이 {pair[1]}일선 {above ? "위 (골든크로스 구간)" : "아래 (데드크로스 구간)"}
        </span>
      )}
      <span className="inline-flex items-center gap-1 rounded-md border border-up/30 px-2 py-1"><span className="text-up">▲ 최근 골든크로스</span><b className="tabular">{when(lastG)}</b><span className="text-muted-foreground">{ago(lastG)}</span></span>
      <span className="inline-flex items-center gap-1 rounded-md border border-down/30 px-2 py-1"><span className="text-down">▼ 최근 데드크로스</span><b className="tabular">{when(lastD)}</b><span className="text-muted-foreground">{ago(lastD)}</span></span>
      <span className="text-muted-foreground">{rangeLabel ? `선택 기간(${rangeLabel})` : "표시 구간"} 내 골든 {inRange.filter((c) => c.kind === "golden").length}회 · 데드 {inRange.filter((c) => c.kind === "death").length}회</span>
    </div>
  );
}

export default function StockPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = decodeURIComponent(params.ticker).toUpperCase();
  const { settings } = useSettings();
  // User choices override the saved preferences (derived, no effect-driven syncing).
  const [rangeSel, setRange] = useState<string | null>(null);
  const [interval, setInterval_] = useState<string>("");
  const [vpSel, setVp] = useState<string | null>(null);
  const [years, setYears] = useState(3);
  const [indSel, setInd] = useState<Record<string, boolean>>({});
  const [crossSel, setCrossSel] = useState("20-60");
  const range = rangeSel ?? settings?.preferences.default_range ?? "1Y";
  const vp = vpSel ?? settings?.preferences.volume_profile_period ?? "6M";
  const ind = useMemo(() => ({ ...(settings?.preferences.indicators ?? {}), ...indSel }), [settings, indSel]);
  const crossPair = CROSS_PAIRS.find((c) => c.value === crossSel)?.pair ?? null;

  const ov = useOverview(ticker);
  // All queries use the ticker as typed (the API resolves aliases like 005930 -> 005930.KS),
  // so sections start loading in parallel instead of waiting for the quote.
  const sym = ticker;
  const canonical = ov.data?.symbol ?? ticker;
  const pr = settings?.preferences;
  const rsiP = useMemo(() => ({ short: pr?.rsi_short_period ?? 7, medium: pr?.rsi_period ?? 14, long: pr?.rsi_long_period ?? 28 }),
    [pr?.rsi_short_period, pr?.rsi_period, pr?.rsi_long_period]);
  const [oscSel, setOscSel] = useState<OscMode | null>(null);
  const oscMode: OscMode = oscSel ?? pr?.lower_osc ?? "stoch";
  const chart = useChart(sym, range, interval || undefined, rsiP, pr?.stoch_sets);
  const tech = useTechnical(sym, vp);
  const scores = useScores(sym);
  const analogs = useAnalogs(sym, years);
  const news = useStockNews(sym);
  const tz = canonical.endsWith(".KS") || canonical.endsWith(".KQ") || /^\d{6}$/.test(canonical) ? "Asia/Seoul" : "America/New_York";
  const currency = ov.data?.quote.currency;

  useEffect(() => { document.title = `${ov.data?.profile.name_ko ?? canonical} · MarketLens`; }, [canonical, ov.data?.profile.name_ko]);
  const enabled = useMemo<Record<string, boolean>>(() => ({ volume: true, trendlines: true, drawdown: true, ...ind }), [ind]);

  return (
    <div className="space-y-5">
      <StockHeader q={ov} scores={scores.data} />
      {ov.isError ? null : (
        <>
          <ScoreStrip scores={scores.data} tech={tech.data} loading={tech.isLoading || scores.isLoading} />
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Section
              className="xl:col-start-1 xl:row-start-1"
              title={<span className="flex items-center gap-2">가격 차트 <span className="font-normal text-muted-foreground">{chart.data ? (chart.data.intraday ? `${chart.data.interval} 봉` : chart.data.interval === "1wk" ? "주봉" : "일봉") : ""}</span></span>}
              right={
                <>
                  <Segmented ariaLabel="기간" size="xs" value={interval ? "" : range} onChange={(v) => { setRange(v); setInterval_(""); }} options={RANGES} />
                  <Segmented ariaLabel="분봉 간격" size="xs" value={interval} onChange={setInterval_} options={INTERVALS} />
                  <Popover>
                    <PopoverTrigger className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"><SlidersHorizontal className="size-3.5" />보조지표</PopoverTrigger>
                    <PopoverContent className="w-64" align="end">
                      <div className="grid grid-cols-1 gap-1.5">
                        {Object.keys(IND_LABELS).map((k) => (
                          <label key={k} className="flex items-center justify-between gap-2 text-xs">
                            <span>{IND_LABELS[k]}</span>
                            <Switch checked={!!enabled[k]} onCheckedChange={(val: boolean) => setInd((s) => ({ ...s, [k]: val }))} />
                          </label>
                        ))}
                        <div className="mt-1 border-t pt-2 text-xs">
                          <div className="mb-1 text-muted-foreground">매물대 기간</div>
                          <Segmented size="xs" value={vp} onChange={setVp} options={[["1M", "1개월"], ["3M", "3개월"], ["6M", "6개월"], ["1Y", "1년"], ["3Y", "3년"]].map(([value, label]) => ({ value, label }))} />
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>
                </>
              }
              bodyClassName="p-2"
            >
              <div className="flex flex-wrap items-center gap-2 px-2 pb-2 text-xs">
                <span className="font-medium">골든/데드크로스 기준</span>
                <Segmented ariaLabel="골든/데드크로스 기준" size="xs" value={crossSel} onChange={setCrossSel} options={CROSS_PAIRS.map((c) => ({ value: c.value, label: c.label }))} />
                <span className="ml-2 font-medium">하단 지표</span>
                <Segmented ariaLabel="하단 지표 종류" size="xs" value={oscMode} onChange={(v) => { setOscSel(v); setInd((s) => ({ ...s, osc3: true })); }}
                  options={[{ value: "stoch", label: "스토캐스틱 단·중·장" }, { value: "rsi", label: "RSI 단·중·장" }]} />
              </div>
              {chart.isLoading ? <div className="h-[560px] p-4"><SkeletonBlock rows={8} /></div> : (
                <QueryState q={chart} label="차트">
                  {(c) => c.bars.length === 0 ? <p className="p-6 text-sm text-muted-foreground">이 기간의 데이터를 불러올 수 없습니다 (휴장 또는 거래 없음).</p> : (
                    <>
                      {enabled.drawdown && c.drawdown && <DrawdownStrip dd={c.drawdown} currency={currency} />}
                      {crossPair && <CrossSummary bars={c.bars} pair={crossPair} rangeLabel={c.intraday ? undefined : c.range}
                        from={c.visible_from ? Math.max(0, c.bars.findIndex((b) => b.time === c.visible_from)) : 0} />}
                      <PriceChart bars={c.bars} intraday={c.intraday} currency={currency} exchangeTz={tz} enabled={enabled}
                        levels={tech.data?.support_resistance} volumeProfile={tech.data?.volume_profile} patterns={tech.data?.patterns}
                        cross={crossPair} visibleFrom={c.visible_from} rsiPeriods={c.rsi_periods ?? rsiP} oscMode={oscMode} stochSets={c.stoch_sets ?? pr?.stoch_sets}
                        trendlines={c.trendlines}
                        peak={c.drawdown?.peaks[0] ? { price: c.drawdown.peaks[0].peak, pct: c.drawdown.peaks[0].pct, time: c.drawdown.peaks[0].time } : null}
                        height={480 + (enabled.osc3 ? 3 * OSC_H : 0) + (enabled.rsi ? 140 : 0) + 96 * ["macd", "stoch", "atr", "adx", "obv"].filter((k) => enabled[k]).length} />
                      {enabled.trendlines && <TrendlineSummary c={c} currency={currency} />}
                      <div className="flex flex-wrap items-center gap-2 px-2 pt-1 text-[11px] text-muted-foreground">
                        <span>출처 {c.source}</span>
                        {c.intraday && <Pill>거래소 현지 시각 기준 · 흐린 캔들 = 장전/장후 거래</Pill>}
                        <UpdatedAt at={c.fetched_at} />
                      </div>
                    </>
                  )}
                </QueryState>
              )}
            </Section>
            {/* Horizons sit directly under the chart on every screen width; the signal panel moves beside the chart on xl. */}
            <div className="space-y-3 xl:col-span-2 xl:row-start-2">
              <OutlookPanel symbol={sym} />
              {tech.isError ? null : <HorizonPanel tech={tech.data} />}
            </div>
            <div className="xl:col-start-2 xl:row-start-1"><SignalPanel tech={tech.data} scores={scores.data} /></div>
          </div>
          <Section title="애널리스트 · 리포트" subtitle={sym.endsWith(".KS") || sym.endsWith(".KQ") || /^\d{6}$/.test(sym) ? "증권사 리포트 최근 5건 · 평균 목표가 · 의견 흐름" : "월가 애널리스트 최근 의견 5건 · 평균 목표가 · 의견 흐름"}>
            <AnalystPanel symbol={canonical} />
          </Section>
          {tech.isError ? <Section title="기술적 분석"><QueryState q={tech} label="기술적 분석">{() => null}</QueryState></Section> : tech.data ? (
            <div className="grid gap-3 lg:grid-cols-3">
              <LevelsPanel tech={tech.data} currency={currency} />
              <TrendPanel tech={tech.data} />
              <PatternsPanel tech={tech.data} />
            </div>
          ) : <div className="grid gap-3 lg:grid-cols-3">{[0, 1, 2].map((i) => <Section key={i} title=" "><SkeletonBlock rows={5} /></Section>)}</div>}
          <AnalogsPanel q={analogs} years={years} setYears={setYears} />
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
            <Section title="최신 뉴스" subtitle="중복 기사를 묶고 시장 영향도 순으로 정렬" right={<UpdatedAt at={news.data?.fetched_at} />}>
              <QueryState q={news} label="뉴스" skeleton={<SkeletonBlock rows={6} />}>
                {(n) => n.items.length === 0 ? <p className="text-sm text-muted-foreground">이 종목의 뉴스를 불러올 수 없습니다.</p> : (
                  <>
                    <ul className="divide-y">{n.items.slice(0, 12).map((it) => <NewsRow key={it.id} n={it} />)}</ul>
                    {n.filings && n.filings.length > 0 && (
                      <div className="mt-3 border-t pt-3">
                        <h3 className="mb-1 text-xs font-semibold">SEC 공시</h3>
                        <ul className="space-y-1 text-xs">{n.filings.slice(0, 6).map((f) => (
                          <li key={f.id} className="flex justify-between gap-2"><a className="hover:underline" href={f.url ?? undefined} target="_blank" rel="noopener noreferrer">{filingKo(f.title)}</a><span className="shrink-0 text-muted-foreground">{f.published_at?.slice(0, 10)}</span></li>
                        ))}</ul>
                      </div>
                    )}
                  </>
                )}
              </QueryState>
            </Section>
            <div className="space-y-3">
              <Section title="뉴스 해석">
                <QueryState q={news} label="뉴스 요약">
                  {(n) => n.summary ? <SummaryBlock s={n.summary} title={n.summary.method === "ai" ? "AI 해석" : "뉴스 요약"} /> : <p className="text-sm text-muted-foreground">요약을 불러올 수 없습니다</p>}
                </QueryState>
              </Section>
              <Section title="이 종목의 매크로 환경" subtitle={scores.data?.macro_detail?.formula}>
                <QueryState q={scores} label="매크로 분석">
                  {(s) => (
                    <div className="space-y-3 text-sm">
                      <div className="flex items-baseline gap-2"><span className="text-2xl font-semibold tabular">{s.macro?.toFixed(0) ?? "—"}</span><span className="text-xs text-muted-foreground">/100 매크로 정합도</span></div>
                      <div><div className="mb-1 text-xs font-semibold text-up">순풍 요인</div><ul className="space-y-1 text-xs">{s.macro_detail.reasons.map((r, i) => <li key={i}>· {r}</li>)}</ul></div>
                      <div><div className="mb-1 text-xs font-semibold text-down">역풍 요인</div><ul className="space-y-1 text-xs">{s.macro_detail.risks.length ? s.macro_detail.risks.map((r, i) => <li key={i}>· {r}</li>) : <li className="text-muted-foreground">해당 없음</li>}</ul></div>
                      {s.macro_detail.empirical && (
                        <div className="rounded-lg border p-2 text-xs">
                          <div className="font-medium">실측 민감도 (2년 일간 회귀, R² {s.macro_detail.empirical.r2.toFixed(2)}, 시장 베타 {s.macro_detail.empirical.market_beta.toFixed(2)})</div>
                          <ul className="mt-1 space-y-0.5 text-muted-foreground">{s.macro_detail.empirical.details.map((d) => <li key={d.factor}>{d.factor}: {d.sensitivity} · 최근 60일 {d.move} → 추정 {d.effect_pct >= 0 ? "+" : ""}{d.effect_pct.toFixed(1)}%</li>)}</ul>
                        </div>
                      )}
                    </div>
                  )}
                </QueryState>
              </Section>
            </div>
          </div>
          {tz === "Asia/Seoul" && <DartPanel symbol={canonical} />}
          <FundamentalsPanel symbol={sym} currency={currency} />
          <BullBear tech={tech.data} scores={scores.data} />
          <p className="pb-4 text-center text-[11px] text-muted-foreground">{scores.data?.disclaimer ?? "점수는 과거 데이터에 규칙을 적용한 요약이며, 미래 예측이나 투자 권유가 아닙니다."}</p>
        </>
      )}
    </div>
  );
}
