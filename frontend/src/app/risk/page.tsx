"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Info } from "lucide-react";
import { useMemo, useState } from "react";

import { SignalChart, type Band } from "@/components/charts/signal-chart";
import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock, Stat, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { frac, num } from "@/lib/format";
import { STATIC } from "@/lib/static";

interface Pt { date: string; value: number }
interface Decline { peak: string; onset: string; trough: string; depth: number; open: boolean }
interface After { dd: number | null; days: number; complete: boolean }
interface CreditEvent {
  signal_date: string; date: string; gap: number; b: number; ccc: number; spx: number; after: Record<string, After>;
  decline: (Decline & { timing: "before_peak" | "before_onset" | "after_onset"; lead_days: number }) | null;
}
type BaseRate = Record<string, { share: number | null; days: number }>;
interface Credit {
  threshold: number; rearm_days: number; swing: number; fred_live: boolean; range: [string, string]; observations: number;
  latest: { date: string; b: number; ccc: number; gap: number; above: boolean; days_above: number; percentile: number | null; max: number; max_date: string; min: number; min_date: string };
  first_day_above: boolean; last_crossing: string | null; events: CreditEvent[]; base_rate: BaseRate; declines: Decline[];
  series: { gap: Pt[]; b: Pt[]; ccc: Pt[]; spx: Pt[] }; imported_rows: number; updated_at: string;
}
type Measure = "cor1m" | "cor3m" | "realized";
interface Level { before: number | null; peak: number | null; onset: number | null; max: number | null; max_date: string | null }
type CorrDecline = Decline & Partial<Record<Measure, Level>>;
interface Th { level: number; direction: "above" | "below"; signals: number; complete: number; fell_10_in_6m: number; share: number | null; already_falling: number; dates: string[] }
interface Corr {
  window: number; swing: number; rearm_days: number; missing: string[];
  latest: Partial<Record<Measure, { value: number; date: string; percentile: number | null; change_1m: number | null; since: string }>>;
  declines: CorrDecline[]; thresholds: Partial<Record<Measure, Th[]>>; base_rate: BaseRate;
  series: Partial<Record<Measure | "spx", Pt[]>>; updated_at: string; status?: string;
}

const MEASURE: Record<Measure, { label: string; short: string; color: string }> = {
  cor1m: { label: "내재 상관 1개월 (Cboe COR1M)", short: "COR1M", color: "--s1" },
  cor3m: { label: "내재 상관 3개월 (Cboe COR3M)", short: "COR3M", color: "--s2" },
  realized: { label: "섹터 실현 상관 (63거래일, ×100)", short: "실현 상관", color: "--s3" },
};
const HORIZON_KO: Record<string, string> = { "63": "3개월", "126": "6개월", "252": "12개월" };

const days = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000);
const median = (xs: number[]) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const bandsOf = (ds: Decline[]): Band[] => ds.map((d) => ({ from: d.peak, to: d.trough }));
const fmtPct2 = (v: number) => `${v.toFixed(2)}%`;
const fmt1 = (v: number) => v.toFixed(1);

function AfterCell({ a }: { a?: After }) {
  if (!a || a.dd === null) return <td className="px-2 py-2 text-right text-muted-foreground">—</td>;
  return (
    <td className={cn("px-2 py-2 text-right tabular", a.dd <= -0.1 ? "text-down" : "")}>
      {frac(a.dd, 1)}{!a.complete && <span className="block text-[10px] text-muted-foreground">{a.days}거래일 경과</span>}
    </td>
  );
}

function timingText(e: CreditEvent) {
  const d = e.decline;
  if (!d) return "12개월 안에 10% 하락 없음";
  if (d.timing === "before_peak") return `고점보다 ${days(e.signal_date, d.peak)}일 먼저 · −10%까지 ${d.lead_days}일`;
  if (d.timing === "before_onset") return `고점 ${days(d.peak, e.signal_date)}일 뒤 · −10%까지 ${d.lead_days}일`;
  return `이미 −10% 하락 뒤 (${-d.lead_days}일 늦음)`;
}

// ------------------------------------------------------------ credit
function CreditView({ c, draft, setDraft, apply }: { c: Credit; draft: string; setDraft: (v: string) => void; apply: () => void }) {
  const L = c.latest;
  const marks = useMemo(() => c.events.map((e) => ({ date: e.signal_date, text: "돌파" })), [c]);
  const spxMarks = useMemo(() => c.events.map((e) => ({ date: e.date, text: "신호" })), [c]);
  const bands = useMemo(() => bandsOf(c.declines), [c]);
  const gapLines = useMemo(() => [{ name: "CCC − B", color: "--s2", points: c.series.gap }], [c]);
  const legLines = useMemo(() => [{ name: "CCC 이하", color: "--s1", points: c.series.ccc }, { name: "B", color: "--s2", points: c.series.b }], [c]);
  const spxLines = useMemo(() => [{ name: "S&P 500", color: "--foreground", points: c.series.spx, width: 1 as const }], [c]);
  const th = useMemo(() => [{ value: c.threshold, label: `기준 ${c.threshold}` }], [c]);
  const br = c.base_rate["126"];
  const partial = c.range[0] > "2000-01-01";
  return (
    <Section title="하이일드 B vs CCC 스프레드" subtitle={<>ICE BofA 옵션조정 스프레드(OAS). <b className="text-foreground">간격 = CCC 이하 등급 − B 등급</b>. 가장 약한 기업들의 조달 비용이 한 단계 위 등급보다 얼마나 더 벌어졌는지를 봅니다. 기준선 아래에 {c.rearm_days}거래일 이상 있다가 위로 넘은 날을 돌파로 셉니다.</>}
      right={<UpdatedAt at={c.updated_at} source={c.fred_live ? "FRED (ICE BofA)" : "저장된 기록 (FRED 응답 없음)"} />}>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="CCC 이하 OAS" value={fmtPct2(L.ccc)} sub={L.date} />
        <Stat label="B OAS" value={fmtPct2(L.b)} sub={L.date} />
        <Stat label="간격 (CCC − B)" value={<span className={L.above ? "text-down" : ""}>{num(L.gap, 2)}%p</span>} sub={L.above ? `기준 위 · ${L.days_above}거래일째` : "기준 아래"} />
        <Stat label="보유 기록 중 백분위" value={L.percentile === null ? "—" : `${L.percentile.toFixed(0)}`} sub={`${c.range[0]} 이후`} />
        <Stat label="기록 중 최고 / 최저" value={`${num(L.max, 2)} / ${num(L.min, 2)}`} sub={`${L.max_date} / ${L.min_date}`} />
        <Stat label="마지막 돌파" value={c.last_crossing ?? "없음"} sub={`기준 ${c.threshold}%p`} />
      </div>

      <form className="mt-4 flex flex-wrap items-center gap-2 text-xs" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <label htmlFor="th" className="text-muted-foreground">기준선 (%p)</label>
        <input id="th" type="number" step="0.1" min="1" max="30" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={STATIC}
          className="h-8 w-20 rounded-md border bg-background px-2 tabular disabled:opacity-60" />
        <button type="submit" disabled={STATIC} className="h-8 rounded-md border px-3 font-medium hover:bg-accent disabled:opacity-60">적용</button>
        {STATIC && <span className="text-muted-foreground">데모에서는 7.8%p로 고정돼 있습니다.</span>}
      </form>

      <div className="mt-4 grid gap-5 xl:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-semibold">간격 (CCC − B, %p)</div>
          <SignalChart lines={gapLines} thresholds={th} marks={marks} bands={bands} height={240} format={fmt1} />
        </div>
        <div>
          <div className="mb-1 text-xs font-semibold">S&amp;P 500 (같은 기간)</div>
          <SignalChart lines={spxLines} marks={spxMarks} bands={bands} height={240} format={(v) => v.toFixed(0)} />
        </div>
      </div>
      <div className="mt-4">
        <div className="mb-1 text-xs font-semibold">등급별 스프레드 (%)</div>
        <SignalChart lines={legLines} bands={bands} height={180} format={fmt1} />
      </div>

      <h3 className="mt-6 text-xs font-semibold">기준 {c.threshold}%p 돌파 뒤 S&amp;P 500</h3>
      {c.events.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">보유한 기록({c.range[0]} ~ {c.range[1]}) 안에서 {c.threshold}%p 돌파가 없었습니다{c.first_day_above ? " (기록 첫날부터 이미 기준 위였습니다)" : ""}.</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[720px] text-xs">
            <thead className="text-muted-foreground">
              <tr className="border-b"><th className="px-2 py-2 text-left font-medium">돌파일</th><th className="px-2 py-2 text-right font-medium">간격</th>
                {Object.keys(HORIZON_KO).map((h) => <th key={h} className="px-2 py-2 text-right font-medium">이후 {HORIZON_KO[h]} 최저</th>)}
                <th className="px-2 py-2 text-left font-medium">이어진 10% 이상 하락</th><th className="px-2 py-2 text-left font-medium">신호 시점</th></tr>
            </thead>
            <tbody>
              {c.events.map((e) => (
                <tr key={e.signal_date} className="border-b border-border/60">
                  <td className="px-2 py-2 tabular">{e.signal_date}</td>
                  <td className="px-2 py-2 text-right tabular">{num(e.gap, 2)}%p</td>
                  {Object.keys(HORIZON_KO).map((h) => <AfterCell key={h} a={e.after[h]} />)}
                  <td className="px-2 py-2 tabular">{e.decline ? <>{e.decline.peak} → {e.decline.trough} <span className="text-down">{frac(e.decline.depth, 1)}</span>{e.decline.open && <Pill tone="warn" className="ml-1">진행 중</Pill>}</> : "—"}</td>
                  <td className="px-2 py-2">{timingText(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className="mt-6 text-xs font-semibold">같은 기간의 S&amp;P 500 10% 이상 하락</h3>
      <ul className="mt-2 space-y-1 text-sm">
        {c.declines.map((d) => {
          const sig = c.events.find((e) => e.signal_date <= d.onset && days(e.signal_date, d.onset) <= 365);
          return (
            <li key={d.peak} className="tabular">
              {d.peak} 고점 → {d.onset} −10% 도달 → {d.trough} {d.open ? "현재까지 저점" : "저점"} <span className="text-down">{frac(d.depth, 1)}</span>
              <span className="text-muted-foreground"> · {sig ? `${sig.signal_date} 돌파 뒤 ${days(sig.signal_date, d.onset)}일` : `앞선 1년 안에 ${c.threshold}%p 돌파 없음`}</span>
            </li>
          );
        })}
        {!c.declines.length && <li className="text-muted-foreground">없음</li>}
      </ul>
      {br?.share !== null && br?.share !== undefined && (
        <p className="mt-3 text-xs text-muted-foreground">비교 기준: 같은 기간 아무 날이나 골랐을 때 이후 6개월 안에 그날보다 10% 이상 낮게 마감한 날이 있었던 비율은 {(br.share * 100).toFixed(0)}%입니다 ({br.days}거래일).</p>
      )}
      {partial && (
        <p className="mt-3 flex gap-1.5 rounded-lg border border-dashed p-3 text-[11.5px] leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 size-3.5 shrink-0" />
          <span>ICE 라이선스 때문에 FRED는 B·CCC 스프레드를 최근 3년치만 공개합니다. 그래서 {c.range[0]} 이전 기록은 여기서 확인할 수 없습니다.
            이 앱은 받아 온 값을 계속 저장하므로 기록은 날마다 길어집니다. 블룸버그 등에서 과거 데이터를 내려받아 <code>backend/data/credit_spreads_import.csv</code>(열: date, b, ccc, 단위 %)로 넣으면 합쳐서 다시 계산합니다{c.imported_rows ? ` (지금 ${c.imported_rows}행 반영됨)` : ""}.
            지금 보이는 돌파는 {c.events.length}번뿐이라 통계라고 할 수 없습니다.</span>
        </p>
      )}
    </Section>
  );
}

// ------------------------------------------------------------ correlation
function CorrView({ r }: { r: Corr }) {
  const avail = (Object.keys(MEASURE) as Measure[]).filter((k) => r.series[k]?.length);
  const [m, setM] = useState<Measure>(avail.includes("cor1m") ? "cor1m" : avail[0]);
  const bands = useMemo(() => bandsOf(r.declines), [r]);
  const lines = useMemo(() => (Object.keys(MEASURE) as Measure[]).filter((k) => r.series[k]?.length)
    .map((k) => ({ name: MEASURE[k].short, color: MEASURE[k].color, points: r.series[k]! })), [r]);
  const spx = useMemo(() => [{ name: "S&P 500", color: "--foreground", points: r.series.spx ?? [], width: 1 as const }], [r]);
  const rows = r.declines.filter((d) => d[m]?.peak !== null && d[m]?.peak !== undefined);
  const done = rows.filter((d) => !d.open);
  const atPeak = median(done.map((d) => d[m]!.peak!)), atMax = median(done.map((d) => d[m]!.max ?? NaN).filter(Number.isFinite));
  const low = done.filter((d) => (d[m]!.peak ?? 99) < 20).length;
  const lateMax = done.filter((d) => d[m]!.max_date && d[m]!.max_date! >= d.onset).length;
  const ths = r.thresholds[m] ?? [];
  const br = r.base_rate["126"];
  return (
    <Section title="시장 상관관계" subtitle={<>종목들이 얼마나 한 방향으로 같이 움직이는지입니다. Cboe 내재 상관은 S&amp;P 500 지수 옵션과 개별 종목 옵션 가격에 담긴 기대치(0~100), 실현 상관은 9개 섹터 ETF 일간 수익률의 {r.window}거래일 평균 상관(×100)입니다. 음영은 S&amp;P 500이 10% 이상 빠진 구간입니다.</>}
      right={<UpdatedAt at={r.updated_at} source="Cboe · Yahoo" />}>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {avail.map((k) => {
          const x = r.latest[k]!;
          return <Stat key={k} label={MEASURE[k].label} value={<>{num(x.value, 1)} {x.change_1m !== null && <span className={cn("text-xs font-normal", x.change_1m > 0 ? "text-down" : "text-muted-foreground")}>(1개월 {x.change_1m > 0 ? "+" : ""}{num(x.change_1m, 1)})</span>}</>}
            sub={`${x.since.slice(0, 4)}년 이후 백분위 ${x.percentile?.toFixed(0) ?? "—"} · ${x.date}`} />;
        })}
      </div>
      {r.missing.length > 0 && <p className="mt-2 text-xs text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable): {r.missing.map((k) => MEASURE[k as Measure]?.short ?? k).join(", ")}</p>}

      <div className="mt-4 grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div><div className="mb-1 text-xs font-semibold">상관 지표 (주간)</div><SignalChart lines={lines} bands={bands} height={280} format={fmt1} /></div>
        <div><div className="mb-1 text-xs font-semibold">S&amp;P 500 (주간)</div><SignalChart lines={spx} bands={bands} height={280} format={(v) => v.toFixed(0)} /></div>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">하락이 올 때 상관은 몇이었고, 몇까지 벌어졌나</h3>
        <Segmented value={m} onChange={setM} ariaLabel="상관 지표" options={avail.map((k) => ({ value: k, label: MEASURE[k].short }))} />
      </div>
      {done.length > 0 && (
        <p className="mt-2 text-sm leading-relaxed">
          {MEASURE[m].short} 기준으로 끝난 하락 {done.length}번: S&amp;P 500 고점 때 값의 중앙값 <b>{atPeak === null ? "—" : num(atPeak, 1)}</b>,
          하락 중 최고치의 중앙값 <b>{atMax === null ? "—" : num(atMax, 1)}</b>. 고점 때 20 미만이었던 경우가 {low}번입니다.
          최고치가 −10% 도달 이후에 나온 경우가 {lateMax}번{lateMax / done.length >= 0.7 ? "이라, 높은 상관은 하락을 앞서기보다 하락과 함께 나타났습니다" : "입니다"}.
        </p>
      )}
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[720px] text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b"><th className="px-2 py-2 text-left font-medium">고점 → 저점</th><th className="px-2 py-2 text-right font-medium">하락폭</th>
              <th className="px-2 py-2 text-right font-medium">고점 1개월 전</th><th className="px-2 py-2 text-right font-medium">고점</th><th className="px-2 py-2 text-right font-medium">−10% 도달일</th>
              <th className="px-2 py-2 text-right font-medium">하락 중 최고 (날짜)</th><th className="px-2 py-2 text-right font-medium">고점 대비 상승</th></tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((d) => {
              const x = d[m]!;
              return (
                <tr key={d.peak} className="border-b border-border/60 tabular">
                  <td className="px-2 py-2">{d.peak} → {d.trough}{d.open && <Pill tone="warn" className="ml-1">진행 중</Pill>}</td>
                  <td className="px-2 py-2 text-right text-down">{frac(d.depth, 1)}</td>
                  <td className="px-2 py-2 text-right">{x.before === null ? "—" : num(x.before, 1)}</td>
                  <td className={cn("px-2 py-2 text-right", (x.peak ?? 99) < 20 && "font-semibold")}>{x.peak === null ? "—" : num(x.peak, 1)}</td>
                  <td className="px-2 py-2 text-right">{x.onset === null ? "—" : num(x.onset, 1)}</td>
                  <td className="px-2 py-2 text-right">{x.max === null ? "—" : num(x.max, 1)} <span className="text-muted-foreground">({x.max_date ?? "—"})</span></td>
                  <td className="px-2 py-2 text-right">{x.max !== null && x.peak !== null ? `+${num(x.max - x.peak, 1)}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3 className="mt-6 text-xs font-semibold">{MEASURE[m].short}이 기준을 넘은 뒤 6개월 안에 S&amp;P 500이 10% 이상 더 빠진 비율</h3>
      <div className="mt-2 grid gap-4 lg:grid-cols-2">
        {(["above", "below"] as const).map((dir) => (
          <div key={dir} className="overflow-x-auto">
            <div className="mb-1 text-[11px] text-muted-foreground">{dir === "above" ? "위로 돌파 (상관이 높아질 때)" : "아래로 하락 (상관이 낮아질 때)"}</div>
            <table className="w-full text-xs">
              <thead className="text-muted-foreground"><tr className="border-b"><th className="px-2 py-1.5 text-left font-medium">기준</th><th className="px-2 py-1.5 text-right font-medium">신호</th>
                <th className="px-2 py-1.5 text-right font-medium">6개월 안 −10%</th><th className="px-2 py-1.5 text-right font-medium">이미 하락 중</th></tr></thead>
              <tbody>
                {ths.filter((t) => t.direction === dir).map((t) => (
                  <tr key={t.level} className="border-b border-border/60 tabular" title={t.dates.join(", ")}>
                    <td className="px-2 py-1.5">{dir === "above" ? "≥" : "<"} {t.level}</td>
                    <td className="px-2 py-1.5 text-right">{t.signals}</td>
                    <td className="px-2 py-1.5 text-right">{t.complete ? <>{t.fell_10_in_6m}/{t.complete} <span className={cn((t.share ?? 0) > (br?.share ?? 1) ? "text-down" : "text-muted-foreground")}>({((t.share ?? 0) * 100).toFixed(0)}%)</span></> : "—"}</td>
                    <td className="px-2 py-1.5 text-right">{t.already_falling}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
      <p className="mt-3 flex gap-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        <span>신호 = 기준 반대편에 {r.rearm_days}거래일 이상 있다가 넘어선 날. “6개월 안 −10%”는 신호일 종가보다 10% 이상 낮게 마감한 날이 126거래일 안에 있었는지이며, 6개월이 아직 지나지 않은 신호는 분모에서 뺐습니다.
          “이미 하락 중”은 S&amp;P 500이 고점 대비 −10%를 찍은 뒤에 나온 신호 수입니다. 비교 기준: 같은 기간 아무 날이나 골랐을 때 그 비율은 {br?.share !== null && br?.share !== undefined ? `${(br.share * 100).toFixed(0)}%` : "—"}입니다.
          빨간 비율이 이 기준보다 높은 경우입니다. 행에 마우스를 올리면 신호 날짜가 보입니다.</span>
      </p>
    </Section>
  );
}

// ------------------------------------------------------------ leading indicators
interface Ind {
  key: string; group: string; label: string; rule: string; since: string | null; on_now: boolean | null; on_share: number | null;
  base: number | null; p_on: number | null; lift: number | null; base_hi: number | null; p_on_hi: number | null; lift_hi: number | null; on_share_hi: number | null;
  declines_seen: number; declines_hit: number; coverage: number | null; median_lead_days: number | null; signals: number; signals_good: number; precision: number | null;
}
interface Leading {
  look_days: number; swing: number; near_high: number; rearm_days: number; declines: { peak: string; trough: string; depth: number; open: boolean }[];
  indicators: Ind[]; matrix: Record<string, (0 | 1 | null)[]>; now: { date: string; on: string[] }; updated_at: string;
}

const p0 = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(0)}%`);
const lx = (v: number | null) => (v === null ? "—" : `${v.toFixed(2)}배`);

function LeadingView({ d }: { d: Leading }) {
  const rows = [...d.indicators].sort((a, b) => (b.lift_hi ?? -1) - (a.lift_hi ?? -1));
  const strong = rows.filter((r) => (r.lift_hi ?? 0) >= 1.5);
  const few = strong.filter((r) => (r.coverage ?? 0) < 0.5).length;
  const common = [...d.indicators].filter((r) => (r.coverage ?? 0) >= 0.65 && r.declines_seen >= 10).sort((a, b) => (b.coverage ?? 0) - (a.coverage ?? 0));
  const inverse = rows.filter((r) => r.lift_hi !== null && r.lift_hi <= 0.8).reverse();
  const both = rows.filter((r) => (r.lift_hi ?? 0) >= 1.5 && (r.coverage ?? 0) >= 0.6);
  const onNow = d.indicators.filter((r) => d.now.on.includes(r.key));
  const base = rows.find((r) => r.base_hi !== null)?.base_hi ?? null;
  return (
    <Section title="다른 선행지표 검증" subtitle={<>지표마다 규칙을 하나씩 미리 정해 두고(흔히 쓰는 기준 그대로, 결과를 보고 고치지 않음), S&amp;P 500이 52주 고점 근처에서 10% 이상 빠지기 시작한 경우(1985년 이후, 지표마다 데이터가 있는 만큼)의 고점 전 6개월 동안 켜져 있었는지와, 켜져 있을 때 실제로 하락이 더 자주 왔는지를 봤습니다.</>}
      right={<UpdatedAt at={d.updated_at} source="FRED · Yahoo · Cboe" />}>
      <div className="space-y-2 text-sm leading-relaxed">
        {strong.length > 0 && <p><b>켜졌을 때 확률이 가장 크게 오른 지표</b>: {strong.map((r) => `${r.label} ${lx(r.lift_hi)} (고점 전 적중 ${r.declines_hit}/${r.declines_seen})`).join(", ")}.{few === strong.length ? " 다만 모두 고점의 절반 이하에서만 미리 켜졌습니다." : few ? ` 이 중 ${few}개는 고점의 절반 이하에서만 미리 켜졌습니다.` : ""}</p>}
        {common.length > 0 && <p><b>고점 전에 가장 자주 켜져 있던 지표</b>: {common.map((r) => `${r.label} ${r.declines_hit}/${r.declines_seen} (${lx(r.lift_hi)})`).join(", ")}. 하락 전 공통점이긴 하지만 평소에도 자주 켜져서, 켜졌다고 하락 확률이 크게 오르지는 않았습니다.</p>}
        {inverse.length > 0 && <p><b>오히려 반대였던 지표</b>: {inverse.map((r) => `${r.label} ${lx(r.lift_hi)}`).join(", ")}. 켜져 있을 때 고점권에서 10% 하락이 평소보다 드물었습니다.</p>}
        <p>{both.length ? `적중률과 확률 배수를 둘 다 갖춘 지표: ${both.map((r) => r.label).join(", ")}.` : "고점 전 적중률 60% 이상이면서 확률도 1.5배 이상 높인 지표는 없었습니다. 하나만 보고 하락을 맞히기는 어렵고, 여러 신호가 겹치는지를 같이 보는 편이 현실적입니다."}</p>
        <p className="text-muted-foreground">기준: 고점권(52주 고점의 3% 이내) 아무 날이나 골랐을 때 6개월 안에 그날보다 10% 이상 낮게 마감한 비율은 {p0(base)}입니다(지표마다 데이터 시작이 달라 조금씩 다름). 지금({d.now.date}) 켜진 경고: {onNow.length ? onNow.map((r) => r.label).join(", ") : "없음"}.</p>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[900px] text-xs">
          <thead className="text-muted-foreground">
            <tr className="border-b">
              <th className="px-2 py-2 text-left font-medium">지표 (규칙)</th><th className="px-2 py-2 text-center font-medium">지금</th>
              <th className="px-2 py-2 text-right font-medium">고점권에서 켜진 날</th><th className="px-2 py-2 text-right font-medium">켜졌을 때 6개월 안 −10%</th>
              <th className="px-2 py-2 text-right font-medium">평소 대비</th><th className="px-2 py-2 text-right font-medium">고점 전 6개월 적중</th>
              <th className="px-2 py-2 text-right font-medium">첫 경고 → 고점 (중앙값)</th><th className="px-2 py-2 text-right font-medium">신호 뒤 6개월 안 −10%</th>
              <th className="px-2 py-2 text-right font-medium">데이터 시작</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-b border-border/60 tabular align-top">
                <td className="px-2 py-2"><div className="font-medium">{r.label} <span className="ml-1 text-[10px] font-normal text-muted-foreground">{r.group}</span></div><div className="text-[11px] text-muted-foreground">{r.rule}</div></td>
                <td className="px-2 py-2 text-center">{r.on_now === null ? "—" : r.on_now ? <Pill tone="warn">켜짐</Pill> : <span className="text-muted-foreground">꺼짐</span>}</td>
                <td className="px-2 py-2 text-right">{p0(r.on_share_hi)}</td>
                <td className="px-2 py-2 text-right">{p0(r.p_on_hi)} <span className="text-muted-foreground">({p0(r.base_hi)})</span></td>
                <td className={cn("px-2 py-2 text-right font-semibold", (r.lift_hi ?? 1) >= 1.5 ? "text-down" : (r.lift_hi ?? 1) <= 0.8 ? "text-up" : "")}>{lx(r.lift_hi)}</td>
                <td className="px-2 py-2 text-right">{r.declines_seen ? `${r.declines_hit}/${r.declines_seen}` : "—"}</td>
                <td className="px-2 py-2 text-right">{r.median_lead_days === null ? "—" : `${r.median_lead_days}일`}</td>
                <td className="px-2 py-2 text-right">{r.signals ? `${r.signals_good}/${r.signals} (${p0(r.precision)})` : "—"}</td>
                <td className="px-2 py-2 text-right text-muted-foreground">{r.since?.slice(0, 4) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="mt-6 text-xs font-semibold">하락별로 보기: 고점 전 6개월 안에 켜져 있었나 (1990년 이후 {d.declines.length}번, 열 위 숫자는 하락폭 %)</h3>
      <div className="mt-2 overflow-x-auto">
        <table className="text-[11px]">
          <thead>
            <tr>
              <th className="sticky left-0 bg-card px-2 py-1 text-left font-medium text-muted-foreground">지표</th>
              {d.declines.map((x) => <th key={x.peak} className="px-1 py-1 font-medium text-muted-foreground" title={`${x.peak} → ${x.trough} ${frac(x.depth, 1)}`}>{x.peak.slice(2, 4)}.{x.peak.slice(5, 7)}<span className="block text-down">{Math.round(x.depth * 100)}</span></th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="sticky left-0 whitespace-nowrap bg-card px-2 py-0.5">{r.label}</td>
                {(d.matrix[r.key] ?? []).map((v, i) => (
                  <td key={i} className="px-1 py-0.5 text-center" title={`${d.declines[i].peak}: ${v === null ? "데이터 없음" : v ? "켜져 있었음" : "꺼져 있었음"}`}>
                    {v === null ? <span className="text-muted-foreground/50">·</span> : <span className={cn("inline-block size-2.5 rounded-full", v ? "bg-foreground" : "border border-muted-foreground/50")} />}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 flex gap-1.5 text-[11.5px] leading-relaxed text-muted-foreground">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        <span>“평소 대비”는 S&amp;P 500이 고점권일 때만 비교했습니다. 약세장 한가운데서는 거의 모든 경고가 다음 하락을 “맞히기” 때문입니다. 월간 지표는 발표 시차(약 3~7주)를 두고 썼지만, FRED는 수정된 최신 값만 줘서 실업수당 청구처럼 자주 수정되는 지표는 실제보다 조금 좋게 나옵니다.
          지표 {d.indicators.length}개를 한꺼번에 시험했고 고점은 {d.declines.length}번뿐이라, 1.5배 안팎의 차이는 우연일 수 있습니다. 과거 통계이며 예측이 아닙니다.</span>
      </p>
    </Section>
  );
}

export default function RiskPage() {
  const [th, setTh] = useState(7.8);
  const [draft, setDraft] = useState("7.8");
  const credit = useQuery({ queryKey: ["risk-credit", th], queryFn: () => api<Credit>(`/api/market/risk/credit?threshold=${th}`), staleTime: 600_000 });
  const corr = useQuery({ queryKey: ["risk-corr"], queryFn: () => api<Corr>("/api/market/risk/correlation"), staleTime: 1_800_000 });
  const lead = useQuery({ queryKey: ["risk-leading"], queryFn: () => api<Leading>("/api/market/risk/leading"), staleTime: 3_600_000 });
  const apply = () => { const v = Number(draft); if (Number.isFinite(v) && v >= 1 && v <= 30) setTh(Math.round(v * 100) / 100); else setDraft(String(th)); };
  return (
    <div className="space-y-5">
      <PageHeader title="리스크 신호" description="하이일드 CCC−B 스프레드와 시장 상관관계를 매일 확인하고, 기준을 넘은 뒤 S&P 500에 실제로 10% 이상 하락이 왔는지 과거 기록과 대조합니다. 결과는 과거 통계이며 예측이 아닙니다." />
      <QueryState q={credit} label="하이일드 스프레드" skeleton={<Section title="하이일드 B vs CCC 스프레드"><SkeletonBlock rows={8} /></Section>}>
        {(c) => <CreditView c={c} draft={draft} setDraft={setDraft} apply={apply} />}
      </QueryState>
      <QueryState q={corr} label="시장 상관관계" skeleton={<Section title="시장 상관관계"><SkeletonBlock rows={8} /></Section>}>
        {(r) => (r.status ? <Section title="시장 상관관계"><p className="text-sm text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable)</p></Section> : <CorrView r={r} />)}
      </QueryState>
      <QueryState q={lead} label="선행지표 검증" skeleton={<Section title="다른 선행지표 검증"><SkeletonBlock rows={10} /></Section>}>
        {(d) => <LeadingView d={d} />}
      </QueryState>
    </div>
  );
}
