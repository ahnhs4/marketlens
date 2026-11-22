"use client";

import { cn } from "cn";
import { AlertTriangle, ArrowRight, History, Info } from "lucide-react";

import {
  Pill, ReasonList, ScoreMeter, ScoreTile, Section, SignalBadge, SkeletonBlock, Unavailable, ViewBadge, WhyButton, scoreTone,
} from "@/components/common";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { frac, isNum, num, pct, price } from "@/lib/format";
import {
  bandKo, componentKo, directionKo, patternKo, pillarKo, quadrantKo, regimeKo, signalKo, statusKo, structureKo, zoneKo,
} from "@/lib/i18n";
import type { Analogs, Component, Horizon, Num, Scores, Technical } from "@/lib/types";

const PCT_FIELDS = new Set(["revenue_growth", "earnings_growth", "roe", "gross_margin", "operating_margin", "fcf_yield"]);

// --------------------------------------------------------------- score strip
export function ScoreStrip({ scores, tech, loading }: { scores?: Scores; tech?: Technical; loading: boolean }) {
  if (loading && !scores && !tech) return <div className="grid grid-cols-2 gap-2 md:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-24 rounded-xl border bg-card p-3.5 shadow-card"><SkeletonBlock rows={2} /></div>)}</div>;
  const sig = tech?.signal;
  const mac = scores?.macro_detail;
  const fund = scores?.fundamental_detail;
  const macroComponents: Component[] = [
    ...(mac?.group?.contributions ?? []).map((c, i) => ({ key: `g${i}`, label: `${c.move}`, score: 50 + c.points, points: c.points, details: [c.evidence] })),
    ...(mac?.empirical ? [{ key: "emp", label: `실측 민감도 (R² ${mac.empirical.r2.toFixed(2)})`, score: mac.empirical.score,
      details: mac.empirical.details.map((d) => `${d.factor} ${d.move}: ${d.sensitivity} → ${d.effect_pct >= 0 ? "+" : ""}${d.effect_pct.toFixed(1)}%`) }] : []),
  ];
  const fundComponents: Component[] = Object.entries(fund?.pillars ?? {}).map(([k, v]) => ({
    key: k, label: pillarKo(k), score: v.score ?? 50,
    details: v.items.map((i) => `${i.label}: ${PCT_FIELDS.has(i.field) ? frac(i.value) : num(i.value, 2)} → ${i.score.toFixed(0)}점`),
  }));
  const blendName: Record<string, string> = { technical: "기술", macro: "매크로", fundamental: "펀더멘털" };
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <ScoreTile label="종합 점수" score={scores?.overall} band={scores?.overall_band}
        sub={scores ? `가중치 ${Object.entries(scores.weights_used).map(([k, v]) => `${blendName[k] ?? k} ${Math.round(v * 100)}%`).join(" · ")}` : "계산 중…"} />
      <ScoreTile label="기술적 점수" score={sig?.score} band={sig?.band}
        why={sig && <WhyButton components={sig.components} title="기술적 점수 구성" formula={sig.formula} extra={sig.regime_note ? <div className="mb-1">{sig.regime_note}</div> : null} />}
        sub={sig ? <SignalBadge signal={sig.signal} size="sm" /> : null} />
      <ScoreTile label="매크로 점수" score={scores?.macro} why={macroComponents.length ? <WhyButton components={macroComponents} title="매크로 점수 요인" formula={mac?.formula} /> : undefined}
        sub={mac?.regime ? `${regimeKo(mac.regime.label)} · ${quadrantKo(mac.regime.quadrant)}` : scores ? "데이터 없음" : "계산 중…"} />
      <ScoreTile label="펀더멘털 점수" score={scores?.fundamental} band={fund?.band}
        why={fundComponents.length ? <WhyButton components={fundComponents} title="펀더멘털 항목별 점수" formula="밸류에이션 30% · 성장성 25% · 수익성 30% · 재무 건전성 15% (없는 지표는 제외)" /> : undefined}
        sub={fund?.missing?.length ? `${fund.missing.length}개 지표 데이터 없음` : undefined} />
    </div>
  );
}

// ------------------------------------------------- short / medium / long horizons
type IndRow = [string, string];
function v(n: Num | undefined, f: (x: number) => string): string {
  return isNum(n) ? f(n) : "—";
}

function keyIndicators(tech: Technical, h: "short" | "medium" | "long"): IndRow[] {
  const i = tech.indicators;
  const c = i.close;
  const vs = (ma: Num | undefined) => (isNum(c) && isNum(ma) ? `${((c / ma - 1) * 100).toFixed(1)}%` : "—");
  const rp = tech.rsi_periods ?? { short: 7, medium: 14, long: 28 };
  const rsiTxt = (x: number) => `${x.toFixed(1)}${x >= 70 ? " · 과매수" : x <= 30 ? " · 과매도" : ""}`;
  if (h === "short") {
    return [
      [`RSI 단기(${rp.short})`, v(i.rsi_short, rsiTxt)],
      ["스토캐스틱 %K", v(i.stoch_k, (x) => x.toFixed(0))],
      ["5일 수익률", v(i.mom5, (x) => frac(x))],
      ["20일 수익률", v(i.mom20, (x) => frac(x))],
      ["SMA5 vs SMA20", isNum(i.sma5) && isNum(i.sma20) ? (i.sma5 > i.sma20 ? "5일선 위 (단기 정배열)" : "5일선 아래 (단기 역배열)") : "—"],
      ["거래량 / 20일 평균", v(i.vol_ratio, (x) => `${x.toFixed(2)}배`)],
      ["가까운 지지 / 저항", `${pct(tech.support_resistance.nearest_support_pct, 1)} / +${tech.support_resistance.nearest_resistance_pct.toFixed(1)}%`],
    ];
  }
  if (h === "medium") {
    return [
      [`RSI 중기(${rp.medium})`, v(i.rsi, rsiTxt)],
      ["MACD / 시그널", `${v(i.macd, (x) => x.toFixed(2))} / ${v(i.macd_signal, (x) => x.toFixed(2))}`],
      ["MACD 히스토그램", v(i.macd_hist, (x) => `${x > 0 ? "+" : ""}${x.toFixed(2)}`)],
      ["ADX (+DI / −DI)", `${v(i.adx, (x) => x.toFixed(0))} (${v(i.plus_di, (x) => x.toFixed(0))} / ${v(i.minus_di, (x) => x.toFixed(0))})`],
      ["60일 수익률", v(i.mom60, (x) => frac(x))],
      ["주가 vs SMA60", vs(i.sma60)],
      ["SMA20 vs SMA60", isNum(i.sma20) && isNum(i.sma60) ? `${((i.sma20 / i.sma60 - 1) * 100).toFixed(1)}%` : "—"],
      ["이평선 배열", structureKo(tech.ma.structure)],
    ];
  }
  return [
    [`RSI 장기(${rp.long})`, v(i.rsi_long, rsiTxt)],
    ["주가 vs SMA200", vs(i.sma200)],
    ["SMA120 vs SMA200", isNum(i.sma120) && isNum(i.sma200) ? `${((i.sma120 / i.sma200 - 1) * 100).toFixed(1)}%` : "—"],
    ["6개월 수익률", v(i.mom120, (x) => frac(x))],
    ["12개월 수익률", v(i.mom250, (x) => frac(x))],
    ["52주 고점 대비", isNum(c) && isNum(i.hi52) ? `${((c / i.hi52 - 1) * 100).toFixed(1)}%` : "—"],
    ["연간 변동성(20일)", v(i.vol20, (x) => `${(x * 100).toFixed(0)}%`)],
  ];
}

export function HorizonPanel({ tech }: { tech?: Technical }) {
  if (!tech) {
    return <div className="grid gap-3 lg:grid-cols-3">{[0, 1, 2].map((k) => <div key={k} className="rounded-xl border bg-card p-4"><SkeletonBlock rows={6} /></div>)}</div>;
  }
  const hz = tech.signal.horizons;
  const META = { short: { title: "단기", sub: "1~20 거래일 · 5·20일선, RSI, 거래량" }, medium: { title: "중기", sub: "20~120 거래일 · 20·60·120일선, MACD, ADX" }, long: { title: "장기", sub: "120 거래일 이상 · 200일선, 6~12개월 추세" } } as const;
  return (
    <section aria-label="단기·중기·장기 지표" className="grid gap-3 lg:grid-cols-3">
      {(["short", "medium", "long"] as const).map((k) => {
        const h: Horizon = hz[k];
        return (
          <div key={k} className="rounded-xl border bg-card">
            <header className="flex items-start justify-between gap-2 border-b px-4 py-3">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold">{META[k].title} 전망</h2>
                <p className="text-[11px] text-muted-foreground">{META[k].sub}</p>
              </div>
              <div className="shrink-0 text-right">
                <div className="flex items-center justify-end gap-2">
                  <span className={cn("text-2xl font-semibold tabular", scoreTone(h.score))}>{h.score.toFixed(0)}</span>
                  <ViewBadge view={h.view} />
                </div>
                <div className="text-[10px] text-muted-foreground">/100 · {bandKo(h.band)}</div>
              </div>
            </header>
            <div className="space-y-3 p-4">
              <ScoreMeter score={h.score} />
              <div>
                <div className="mb-1 text-[11px] font-medium text-muted-foreground">핵심 지표</div>
                <dl className="grid grid-cols-1 gap-y-1 text-xs">
                  {keyIndicators(tech, k).map(([label, val]) => (
                    <div key={label} className="flex justify-between gap-2 border-b border-dashed border-border/60 pb-0.5">
                      <dt className="text-muted-foreground">{label}</dt><dd className="tabular font-medium">{val}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div>
                <div className="mb-1 text-[11px] font-medium text-muted-foreground">점수 구성 (중립 50 대비 기여도)</div>
                <ul className="space-y-1.5">
                  {h.components.map((c) => (
                    <li key={c.key} className="text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span>{c.label}</span>
                        <span className="flex items-center gap-2 tabular">
                          <span className="text-muted-foreground">{c.score.toFixed(0)}점</span>
                          {isNum(c.points) && <span className={cn("w-10 text-right font-semibold", c.points > 0 ? "text-up" : c.points < 0 ? "text-down" : "text-muted-foreground")}>{c.points > 0 ? "+" : ""}{c.points.toFixed(1)}</span>}
                        </span>
                      </div>
                      <div className="relative mt-0.5 h-1 rounded-full bg-muted" aria-hidden>
                        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                        <div className={cn("absolute inset-y-0 rounded-full", c.score >= 50 ? "left-1/2 bg-up-mark" : "right-1/2 bg-down-mark")} style={{ width: `${Math.abs(c.score - 50)}%` }} />
                      </div>
                      {c.details.filter(Boolean).length > 0 && <p className="mt-0.5 text-[11px] text-muted-foreground">{c.details.filter(Boolean).join(" · ")}</p>}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        );
      })}
    </section>
  );
}

// --------------------------------------------------------------- signal panel
export function SignalPanel({ tech, scores }: { tech?: Technical; scores?: Scores }) {
  if (!tech) return <Section title="매수 / 매도 신호"><SkeletonBlock rows={6} /></Section>;
  const s = tech.signal;
  const prev = s.previous;
  const changed = prev ? Object.entries(prev.components).map(([k, val]) => ({ k, from: val, to: s.components.find((c) => c.key === k)?.score ?? val }))
    .filter((x) => Math.abs(x.to - x.from) >= 5).sort((a, b) => Math.abs(b.to - b.from) - Math.abs(a.to - a.from)) : [];
  const macroRisks = scores?.macro_detail?.risks ?? [];
  return (
    <Section title="매수 / 매도 신호" subtitle={`${tech.as_of} 종가 기준 · 시장 국면 ${tech.regime ? regimeKo(tech.regime) : "—"}`}
      right={<WhyButton components={s.components} title="신호 산출 근거" formula={s.formula} />}>
      <div className="flex items-center gap-3">
        <SignalBadge signal={s.signal} size="lg" />
        <span className={cn("text-2xl font-semibold tabular", scoreTone(s.score))}>{s.score.toFixed(0)}</span>
        <span className="text-xs text-muted-foreground">/100 · {bandKo(s.band)}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        매수 ≥ {s.thresholds.buy} · 강력 매수 ≥ {s.thresholds.strong_buy} · 매도 ≤ {s.thresholds.sell} · 강력 매도 ≤ {s.thresholds.strong_sell}
      </p>
      {s.regime_note && <p className="mt-2 flex gap-1.5 text-xs text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" />{s.regime_note}</p>}
      {s.gates.map((g) => <p key={g} className="mt-1 flex gap-1.5 text-xs text-warn"><AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{g}</p>)}
      <div className="mt-4 space-y-4">
        <div>
          <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-up">상승 근거</h3>
          <ReasonList items={s.bullish_factors} tone="up" />
        </div>
        <div>
          <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-down">하락 근거 / 리스크</h3>
          <ReasonList items={[...s.bearish_factors, ...macroRisks.slice(0, 2)]} tone="down" />
        </div>
        {prev && (
          <div className="rounded-lg border bg-background/40 p-3 text-xs">
            <div className="flex items-center gap-1.5 font-medium"><History className="size-3.5" /> 직전 거래일({prev.as_of}) 대비 변화</div>
            <div className="mt-1 flex items-center gap-2 tabular">
              <span>{prev.score.toFixed(0)} <span className="text-muted-foreground">({signalKo(prev.signal)})</span></span>
              <ArrowRight className="size-3" />
              <span className={scoreTone(s.score)}>{s.score.toFixed(0)}</span> <span className="text-muted-foreground">({signalKo(s.signal)})</span>
            </div>
            {changed.length > 0 ? (
              <ul className="mt-1 text-muted-foreground">{changed.slice(0, 4).map((c) => (
                <li key={c.k}>{componentKo(c.k)}: {c.from.toFixed(0)} → <span className={c.to > c.from ? "text-up" : "text-down"}>{c.to.toFixed(0)}</span></li>
              ))}</ul>
            ) : <p className="mt-1 text-muted-foreground">5점 이상 바뀐 구성요소가 없습니다.</p>}
          </div>
        )}
      </div>
    </Section>
  );
}

// --------------------------------------------------------- levels / MA / RSI
export function LevelsPanel({ tech, currency }: { tech: Technical; currency?: string | null }) {
  const sr = tech.support_resistance;
  const vp = tech.volume_profile;
  const row = (l: Technical["support_resistance"]["supports"][number]) => (
    <tr key={l.label} className="border-t">
      <td className={cn("py-1.5 font-semibold", l.label.startsWith("S") ? "text-up" : "text-down")}>{l.label.startsWith("S") ? `지지 ${l.label}` : `저항 ${l.label}`}</td>
      <td className="tabular">{price(l.price, currency)}</td>
      <td className="tabular text-right">{pct(l.distance_pct, 1)}</td>
      <td className="pl-3 text-muted-foreground">
        {l.projected ? <span className="italic">{l.sources[0]}</span> : l.sources.slice(0, 3).join(", ")}
      </td>
    </tr>
  );
  return (
    <Section title="지지 / 저항" subtitle="스윙 고저점, 매물대, 이동평균선, 전고점·전저점을 묶어 산출">
      <table className="w-full text-xs">
        <thead><tr className="text-left text-muted-foreground"><th className="pb-1 font-normal">구분</th><th className="font-normal">가격</th><th className="text-right font-normal">현재가 대비</th><th className="pl-3 font-normal">근거</th></tr></thead>
        <tbody>{[...sr.resistances].reverse().map(row)}{sr.supports.map(row)}</tbody>
      </table>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <Pill tone="up">가까운 지지 {pct(sr.nearest_support_pct, 1)}</Pill>
        <Pill tone="down">가까운 저항 {pct(sr.nearest_resistance_pct, 1)}</Pill>
        {isNum(sr.reward_risk) && <Pill>손익비 {sr.reward_risk.toFixed(2)}</Pill>}
      </div>
      {vp && (
        <div className="mt-3 border-t pt-3 text-xs">
          <div className="font-medium">매물대(볼륨 프로파일) · {tech.volume_profile_period} ({vp.start} → {vp.end})</div>
          <div className="mt-1 grid grid-cols-3 gap-2 tabular">
            <div><span className="text-muted-foreground">최대 거래 가격(POC)</span><div className="font-medium">{price(vp.poc, currency)}</div></div>
            <div><span className="text-muted-foreground">가치영역 상단</span><div className="font-medium">{price(vp.vah, currency)}</div></div>
            <div><span className="text-muted-foreground">가치영역 하단</span><div className="font-medium">{price(vp.val, currency)}</div></div>
          </div>
        </div>
      )}
    </Section>
  );
}

export function TrendPanel({ tech }: { tech: Technical }) {
  const ma = tech.ma;
  const r = tech.rsi;
  const crosses = ma.events.filter((e) => e.event.includes("크로스"));
  const regimeName: Record<string, string> = { compression: "수렴", expansion: "확산", normal: "보통" };
  return (
    <Section title="이동평균선 배열 & RSI" subtitle="SMA 5/20/60/120/200 · RSI(14)">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("text-sm font-semibold", ma.structure.includes("Bullish") ? "text-up" : ma.structure.includes("Bearish") ? "text-down" : "")}>{structureKo(ma.structure)}</span>
        <span className="font-mono text-xs text-muted-foreground">{ma.order}</span>
      </div>
      <ul className="mt-2 grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
        {ma.states.map((s) => (
          <li key={s.state} className="flex items-center justify-between gap-2 rounded border px-2 py-1">
            <span className={s.bullish ? "text-up" : "text-down"}>{s.state}</span><span className="tabular text-muted-foreground">{s.detail}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        <Pill tone={ma.spread_regime === "compression" ? "warn" : "default"}>이평선 {regimeName[ma.spread_regime] ?? ma.spread_regime}{isNum(ma.ma_spread_pct) ? ` (간격 ${ma.ma_spread_pct.toFixed(1)}%)` : ""}</Pill>
        {crosses.length === 0 && <Pill>최근 10거래일 골든/데드크로스 없음</Pill>}
        {ma.events.map((e) => <Pill key={e.event + e.date} tone={e.direction === "bullish" ? "up" : "down"}>{e.event} · {e.bars_ago}일 전</Pill>)}
      </div>
      <div className="mt-4 border-t pt-3">
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">RSI</span>
          <span className={cn("text-xl font-semibold tabular", isNum(r.value) && r.value > 70 ? "text-down" : isNum(r.value) && r.value < 30 ? "text-up" : "")}>{num(r.value, 1)}</span>
          <span className="rounded border px-1.5 py-0.5 text-[11px]">{zoneKo(r.zone)}</span>
          {r.trend && <span className="text-xs text-muted-foreground">추세 {r.trend === "rising" ? "상승" : r.trend === "falling" ? "하락" : "보합"} · 5일 변화 {isNum(r.momentum_5d) ? `${r.momentum_5d > 0 ? "+" : ""}${r.momentum_5d.toFixed(1)}` : "—"}</span>}
        </div>
        <div className="relative mt-2 h-2 rounded-full bg-muted" aria-hidden>
          <div className="absolute inset-y-0 left-[30%] right-[30%] bg-background/60" />
          {isNum(r.value) && <div className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-foreground" style={{ left: `${r.value}%` }} />}
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-muted-foreground"><span>0</span><span>30 과매도</span><span>50</span><span>70 과매수</span><span>100</span></div>
        <p className="mt-2 text-xs text-muted-foreground">{r.context}</p>
        {r.divergences.map((d) => <p key={d.type + d.date} className={cn("mt-1 text-xs", d.direction === "bullish" ? "text-up" : "text-down")}>{d.type} ({d.date}): {d.detail}</p>)}
      </div>
    </Section>
  );
}

export function PatternsPanel({ tech }: { tech: Technical }) {
  return (
    <Section title="차트 패턴" subtitle="일봉 가격 데이터로 탐지 · 과거 성공률은 이 종목의 과거 사례 기준">
      {tech.patterns.length === 0 ? <p className="text-sm text-muted-foreground">최근 15거래일 내 탐지된 패턴이 없습니다.</p> : (
        <ul className="space-y-2">
          {tech.patterns.map((p) => {
            const h = p.historical;
            return (
              <li key={p.pattern + p.date + p.status} className="rounded-lg border p-2.5 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <span className={cn("font-semibold", p.direction === "bullish" ? "text-up" : p.direction === "bearish" ? "text-down" : "")}>{patternKo(p.pattern)}</span>
                    <Pill tone={p.status === "confirmed" ? "primary" : "default"}>{statusKo(p.status)}</Pill>
                  </span>
                  <span className="text-muted-foreground">{p.date} · {p.bars_ago}일 전</span>
                </div>
                <div className="mt-1 grid grid-cols-3 gap-2 tabular">
                  <span><span className="text-muted-foreground">신뢰도 </span>{(p.confidence * 100).toFixed(0)}%</span>
                  <span><span className="text-muted-foreground">예상 방향 </span>{directionKo(p.direction)}</span>
                  <span><span className="text-muted-foreground">과거 성공률 </span>{h && h.sufficient && isNum(h.success_rate) ? `${(h.success_rate * 100).toFixed(0)}% (n=${h.n})` : h ? `n=${h.n} · 표본 부족` : "—"}</span>
                </div>
                <p className="mt-1 text-muted-foreground">{p.description}</p>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">성공 = 다음날 시가에 진입했을 때 20거래일 뒤 예상 방향으로 움직인 경우. 과거 빈도일 뿐 예측이 아닙니다.</p>
    </Section>
  );
}

// --------------------------------------------------------------- analogs
export function AnalogsPanel({ q, years, setYears }: { q: { data?: Analogs; isLoading: boolean; isError: boolean; error: unknown; refetch: () => void }; years: number; setYears: (y: number) => void }) {
  const a = q.data;
  return (
    <Section title="과거 유사 국면 검증" subtitle="과거에 지금과 비슷했던 날들, 그리고 그 이후의 움직임"
      right={<span className="flex gap-1">{[3, 5, 10].map((y) => (
        <button key={y} onClick={() => setYears(y)} className={cn("rounded border px-1.5 py-0.5", years === y ? "bg-accent text-foreground" : "")}>{y}년</button>))}</span>}>
      {q.isLoading ? <SkeletonBlock rows={5} /> : q.isError || !a ? <Unavailable message={(q.error as Error)?.message} onRetry={q.refetch} label="유사 국면" /> : a.status === "insufficient_history" ? (
        <p className="text-sm text-muted-foreground">{a.message}</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-2xl font-semibold tabular">{a.n_matches}</span>
            <span className="text-muted-foreground">건의 독립적인 유사 사례 ({a.window_start} 이후)</span>
            {a.status === "insufficient_sample" && <Pill tone="warn"><AlertTriangle className="size-3" />표본 부족</Pill>}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-xs tabular">
              <thead><tr className="text-left text-muted-foreground">
                <th className="pb-1 font-normal">기간</th><th className="font-normal">상승</th><th className="font-normal">하락</th>
                <th className="font-normal">상승 확률 (95% 신뢰구간)</th><th className="font-normal">기준 확률</th><th className="font-normal">중앙값</th><th className="font-normal">하위25% / 상위25%</th>
              </tr></thead>
              <tbody>
                {Object.values(a.horizons).map((h) => (
                  <tr key={h.horizon} className="border-t">
                    <td className="py-1.5 font-medium">{h.horizon}거래일 후</td>
                    <td className="text-up">{h.positive}</td><td className="text-down">{h.negative}</td>
                    <td>{isNum(h.win_rate) ? `${(h.win_rate * 100).toFixed(1)}%` : "—"}{h.win_rate_ci95 ? <span className="text-muted-foreground"> ({(h.win_rate_ci95[0] * 100).toFixed(0)}–{(h.win_rate_ci95[1] * 100).toFixed(0)}%)</span> : null}
                      {!h.sufficient && <span className="ml-1 text-warn">n&lt;{a.method.min_sample}</span>}</td>
                    <td className="text-muted-foreground">{isNum(h.base_win_rate) ? `${(h.base_win_rate * 100).toFixed(0)}%` : "—"}</td>
                    <td className={isNum(h.median_return) && h.median_return >= 0 ? "text-up" : "text-down"}>{frac(h.median_return)}</td>
                    <td className="text-muted-foreground">{frac(h.p25)} / {frac(h.p75)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground">기준 확률 = 같은 기간 모든 날을 대상으로 한 상승 확률. 유사 사례 확률이 기준보다 높아야 의미가 있습니다.</p>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="text-xs">
              <div className="mb-1 font-medium">현재 상태</div>
              <ul className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-muted-foreground">
                {Object.entries(a.current_features).map(([k, f]) => (
                  <li key={k} className="flex justify-between gap-2"><span>{f.label}</span><span className="tabular text-foreground">{k === "rsi" ? f.value.toFixed(1) : k === "mkt_regime" ? (f.value ? "예" : "아니오") : k.startsWith("vol") ? `${f.value.toFixed(2)}배` : frac(f.value)}</span></li>
                ))}
              </ul>
            </div>
            <div className="text-xs">
              <div className="mb-1 font-medium">가장 비슷했던 과거 날짜</div>
              <ul className="max-h-40 space-y-0.5 overflow-auto pr-1 tabular">
                {[...a.matches].sort((x, y) => x.distance - y.distance).slice(0, 12).map((m) => (
                  <li key={m.date} className="flex justify-between gap-2 text-muted-foreground">
                    <span className="text-foreground">{m.date}</span><span>거리 {m.distance.toFixed(2)}</span>
                    <span>10일 후 <span className={isNum(m.forward["10d"]) && m.forward["10d"] >= 0 ? "text-up" : "text-down"}>{frac(m.forward["10d"])}</span></span>
                    <span>20일 후 <span className={isNum(m.forward["20d"]) && m.forward["20d"] >= 0 ? "text-up" : "text-down"}>{frac(m.forward["20d"])}</span></span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <Popover>
            <PopoverTrigger className="text-[11px] text-primary hover:underline">분석 방법과 왜곡 방지 장치</PopoverTrigger>
            <PopoverContent className="w-96 max-w-[90vw] text-xs">
              <p>{Object.keys(a.method.weights).length}개 지표를 로버스트 z-점수로 표준화한 뒤 가중 거리 {a.method.max_distance} 이하인 날을 찾습니다. 같은 국면이 여러 번 세어지지 않도록 사례 간 최소 {a.method.min_separation_days}거래일 간격을 두고, 오늘과 겹치지 않게 최근 {a.method.embargo_days}거래일은 제외합니다. 결과는 이미 실현된 가격만 사용하며, 기준 확률은 같은 기간 전체 날짜 기준입니다. 시장 국면 기준 지수: {a.benchmark}.</p>
            </PopoverContent>
          </Popover>
          <p className="text-[11px] text-muted-foreground">{a.disclaimer}</p>
        </div>
      )}
    </Section>
  );
}
