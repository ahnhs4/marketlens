"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowRight, CheckCircle2, ChevronDown, CircleDashed, Info, Target, Timer, XCircle } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { isNum, pct } from "@/lib/format";

type Market = "KR" | "US";
type Mode = "auto" | "bull" | "normal" | "bear" | "custom";
interface Cond { key: string; kind: "필" | "충"; label: string; why: string; needs_shares: boolean; enabled: boolean; pass_count: number | null }
interface Strength { excess_20d: number; excess_60d: number; downside_ratio: number | null; rs_vs_high: number; score: number; passed: boolean }
interface Entry { pivot: number; stop: number; risk_pct: number; gap_pct: number; value_surge: number | null; state: string; state_ko: string; passed: boolean }
interface Row {
  symbol: string; name: string; exchange: string; theme: string; close: number; change_pct: number | null; value: number;
  checks: Record<string, boolean | null>; failed: string[]; stage: number; result?: string; strength?: Strength | null; entry?: Entry;
  shares?: { listed: number | null; float: number | null }; theme_rank?: number; theme_peers?: string[];
  plan?: { entry: number; stop: number; stop_pct: number; breakeven_at: number; half_at: number; rules: string[] };
  sparkline?: number[]; dates?: [string, string];
}
interface Flow {
  market: Market; as_of: string; ready: boolean; mode: string | null; enabled: string[];
  market_state: { state: "bull" | "normal" | "bear"; reasons: string[] }; modes: Record<string, string[]>;
  conditions: Cond[]; funnel: { step: number; label: string; count: number; note?: string }[];
  stage1: Row[]; near_miss: { symbol: string; name: string; theme: string; close: number; change_pct: number | null; value: number; failed: string[] }[];
  candidates: Row[]; history: { state: string; message: string | null; done: number; total: number; ready_pct: number; symbols: number };
  updated_at: string;
}

const MODE_KO: Record<string, string> = { bull: "상승장", normal: "평소", bear: "하락장" };
const STEP_ICON = [Target, CheckCircle2, CheckCircle2, CheckCircle2, CheckCircle2];

function money(v: number, m: Market) {
  if (!isNum(v)) return "—";
  return m === "KR" ? (v >= 1e12 ? `${(v / 1e12).toFixed(1)}조` : `${Math.round(v / 1e8).toLocaleString()}억`) : `$${(v / 1e6).toFixed(0)}M`;
}
/** Average move on the index's down days relative to the index (≤ 0 = the stock rose on those days). */
const downside = (v: number | null | undefined) => (!isNum(v) ? "—" : v <= 0.05 ? "하락일에도 버팀(≈0배 이하)" : `${v.toFixed(1)}배`);
const px = (v: number | null | undefined, m: Market) => (!isNum(v) ? "—" : m === "KR" ? Math.round(v).toLocaleString() : v.toFixed(2));

function Mini({ data, pivot, stop }: { data: number[]; pivot: number; stop: number }) {
  if (data.length < 2) return null;
  const w = 260, h = 84;
  const lo = Math.min(...data, stop), hi = Math.max(...data, pivot);
  const y = (v: number) => h - 4 - ((v - lo) / (hi - lo || 1)) * (h - 8);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${y(v)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-20 w-full" preserveAspectRatio="none" role="img" aria-label="최근 120일 종가와 돌파선 · 손절선">
      <line x1={0} x2={w} y1={y(pivot)} y2={y(pivot)} stroke="var(--s2)" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
      <line x1={0} x2={w} y1={y(stop)} y2={y(stop)} stroke="var(--s8)" strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
      <polyline points={pts} fill="none" stroke="var(--foreground)" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Candidate({ r, m }: { r: Row; m: Market }) {
  const e = r.entry!, s = r.strength!, p = r.plan!;
  return (
    <article className="flex flex-col gap-2 rounded-xl border bg-card p-3.5 shadow-card">
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="font-semibold hover:underline">{r.name}</Link>
          <div className="text-[11px] text-muted-foreground">{r.symbol} · {r.theme}</div>
        </div>
        <span className={cn("shrink-0 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold",
          e.state === "breakout" ? "border-up/40 bg-up/10 text-up" : "border-s2/40 bg-s2/10 text-s2")}>{e.state === "breakout" ? "오늘 돌파" : "돌파 대기"}</span>
      </header>
      {r.sparkline && <Mini data={r.sparkline} pivot={e.pivot} stop={e.stop} />}
      <div className="flex gap-3 text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block w-3 border-t border-dashed border-s2" />돌파선(10봉 고점)</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block w-3 border-t border-dashed border-s8" />손절선(10봉 저점)</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs tabular">
        <dt className="text-muted-foreground">현재가</dt><dd className="text-right">{px(r.close, m)} <span className={isNum(r.change_pct) && r.change_pct >= 0 ? "text-up" : "text-down"}>{pct(r.change_pct)}</span></dd>
        <dt className="text-muted-foreground">돌파선까지</dt><dd className="text-right">{e.state === "breakout" ? `돌파 +${e.gap_pct.toFixed(1)}%` : `${e.gap_pct.toFixed(1)}%`}</dd>
        <dt className="text-muted-foreground">손절폭</dt><dd className="text-right">{e.risk_pct.toFixed(1)}% <span className="text-muted-foreground">(≤ 8%)</span></dd>
        <dt className="text-muted-foreground">시장 대비 60일</dt><dd className="text-right text-up">{s.excess_60d >= 0 ? "+" : ""}{s.excess_60d.toFixed(1)}%p</dd>
        <dt className="text-muted-foreground">지수 하락일 낙폭</dt><dd className="text-right">{downside(s.downside_ratio)}</dd>
        <dt className="text-muted-foreground">거래대금 (20일 평균 대비)</dt><dd className="text-right">{money(r.value, m)}{isNum(e.value_surge) ? ` · ${e.value_surge.toFixed(1)}배` : ""}</dd>
      </dl>
      <div className="rounded-md bg-muted/60 p-2 text-xs">
        <div className="mb-1 font-semibold">매매 계획 (규칙대로 계산한 가격)</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 tabular">
          <span className="text-muted-foreground">진입 {e.state === "breakout" ? "(오늘 종가)" : "(돌파 시)"}</span><span className="text-right font-medium">{px(p.entry, m)}</span>
          <span className="text-muted-foreground">손절</span><span className="text-right text-down">{px(p.stop, m)} ({p.stop_pct.toFixed(1)}%)</span>
          <span className="text-muted-foreground">+3% → 손절을 본전으로</span><span className="text-right">{px(p.breakeven_at, m)}</span>
          <span className="text-muted-foreground">+25% → 절반 매도</span><span className="text-right text-up">{px(p.half_at, m)}</span>
        </div>
      </div>
      {r.theme_peers && r.theme_peers.length > 0 && <p className="text-[11px] text-muted-foreground">같은 업종에서 탈락: {r.theme_peers.join(", ")}</p>}
    </article>
  );
}

export default function TrendPage() {
  const qc = useQueryClient();
  const [market, setMarket] = useState<Market>("KR");
  const [mode, setMode] = useState<Mode>("auto");
  const [custom, setCustom] = useState<string[] | null>(null);
  const [step, setStep] = useState(4);
  const [showNear, setShowNear] = useState(false);
  const params = mode === "custom" && custom ? `conds=${custom.join(",")}` : mode === "auto" ? "" : `mode=${mode}`;
  const q = useQuery({ queryKey: ["trend", market, params], queryFn: () => api<Flow>(`/api/trend/flow?market=${market}${params ? `&${params}` : ""}`), staleTime: 300_000 });
  const running = q.data?.history.state === "running";
  const status = useQuery({ queryKey: ["trend-status", market], enabled: running, refetchInterval: 8_000,
    queryFn: () => api<Flow["history"]>(`/api/trend/status?market=${market}`) });
  // When the market-wide download finishes, rerun the flow on the complete data.
  const finished = status.data?.state === "done";
  useEffect(() => { if (finished) qc.invalidateQueries({ queryKey: ["trend"] }); }, [finished, qc]);
  const hist = status.data ?? q.data?.history;

  const d = q.data;
  const enabled = useMemo(() => new Set(custom ?? d?.enabled ?? []), [custom, d]);
  const toggle = (k: string, on: boolean) => {
    const base = new Set(custom ?? d?.enabled ?? []);
    if (on) base.add(k); else base.delete(k);
    setCustom([...base]);
    setMode("custom");
  };
  const rows = (d?.stage1 ?? []).filter((r) => r.stage >= Math.max(1, step));
  const condLabel = (k: string) => d?.conditions.find((c) => c.key === k)?.label ?? k;

  return (
    <div className="space-y-5">
      <PageHeader title="추세추종 플로우"
        description="좋은 종목을 찾는 일이 아니라, 살 수 있는 자리를 기다리는 일. 하루 30분 · 작은 손절, 자주 · 2주~2달 보유 · 확신보다 확률."
        right={<Segmented ariaLabel="시장" value={market} onChange={(v) => { setMarket(v); setCustom(null); setMode("auto"); }}
          options={[{ value: "KR", label: "한국 전 종목" }, { value: "US", label: "미국 S&P500·나스닥100" }]} />} />

      {hist && hist.state === "running" && market === "KR" && (
        <Pill tone="warn"><Timer className="size-3.5" />{hist.message ?? "준비 중"} - 한국 전 종목 일봉을 처음 한 번 내려받는 중입니다. 끝나면 자동으로 다시 계산합니다 ({hist.ready_pct}% 준비됨).</Pill>
      )}

      <QueryState q={q} label="추세추종 플로우" skeleton={<SkeletonBlock rows={10} />}>
        {(d) => !d.ready ? <p className="text-sm text-muted-foreground">가격 이력이 아직 없습니다. 데이터를 내려받는 중입니다.</p> : (
          <>
            <Section title="시장 상태와 조건 세트" subtitle="조건은 시장에 맞춰 건다 - 좋을 땐 다 걸어도 30~50개, 평소엔 [충]을 풀어 10~50개, 하락장엔 풀어도 10개가 안 된다"
              right={<UpdatedAt at={d.updated_at} source={`${d.as_of} 종가 기준`} />}>
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm">지금 시장: <b>{MODE_KO[d.market_state.state]}</b></span>
                <span className="text-xs text-muted-foreground">{d.market_state.reasons.join(" · ")}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <Segmented ariaLabel="조건 세트" size="xs" value={mode} onChange={(v) => { setMode(v); if (v !== "custom") setCustom(null); }}
                  options={[{ value: "auto", label: `자동 (${MODE_KO[d.market_state.state]})` }, { value: "bull", label: "상승장 · 12개" },
                    { value: "normal", label: "평소 · 8개" }, { value: "bear", label: "하락장 · 8개" },
                    ...(mode === "custom" ? [{ value: "custom" as Mode, label: "직접 설정" }] : [])]} />
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                {(["필", "충"] as const).map((kind) => (
                  <div key={kind} className="rounded-lg border p-3">
                    <div className="mb-2 text-xs font-semibold">
                      <span className={cn("mr-1.5 rounded px-1.5 py-0.5", kind === "필" ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground")}>{kind}</span>
                      {kind === "필" ? "필수 조건 · 항상 켬" : "충분 조건 · 시장 따라 켜고 끔"}
                    </div>
                    <ul className="space-y-2">
                      {d.conditions.filter((c) => c.kind === kind).map((c) => (
                        <li key={c.key} className="flex items-start justify-between gap-3">
                          <div className="min-w-0 text-sm">
                            <div>{c.label}</div>
                            <div className="text-[11px] text-muted-foreground">{c.why}{isNum(c.pass_count) ? ` · 단독 통과 ${c.pass_count.toLocaleString()}종목` : c.needs_shares ? " · 앞 조건 통과 종목만 주식수 조회" : ""}</div>
                          </div>
                          <Switch checked={enabled.has(c.key)} onCheckedChange={(v: boolean) => toggle(c.key, v)} aria-label={c.label} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="종목 선정 4단계" subtitle="단계를 누르면 그 단계까지 통과한 종목이 아래에 나옵니다">
              <ol className="flex flex-wrap items-stretch gap-1.5">
                {d.funnel.map((f, i) => {
                  const Icon = STEP_ICON[i];
                  return (
                    <li key={f.step} className="flex items-center gap-1.5">
                      <button onClick={() => setStep(f.step)} disabled={f.step === 0}
                        className={cn("min-w-36 rounded-lg border px-3 py-2 text-left transition-colors", step === f.step ? "border-primary bg-primary/5" : "hover:bg-accent/50", f.step === 0 && "cursor-default")}>
                        <div className="flex items-center gap-1 text-[11px] text-muted-foreground"><Icon className="size-3.5" />{f.step === 0 ? "전체" : `${"①②③④"[f.step - 1]}`} {f.label}</div>
                        <div className="text-2xl font-semibold tabular">{f.count.toLocaleString()}</div>
                        {f.note && <div className="text-[10px] text-muted-foreground">{f.note}</div>}
                      </button>
                      {i < d.funnel.length - 1 && <ArrowRight className="size-4 shrink-0 text-muted-foreground" />}
                    </li>
                  );
                })}
              </ol>
              <p className="mt-2 flex gap-1.5 text-[11px] text-muted-foreground"><Info className="mt-0.5 size-3 shrink-0" />
                ② 시장보다 강한가 = 60일 수익률이 지수보다 높고, 지수 하락일 평균 낙폭이 지수의 2.5배 이내 · ③ 타점 = 돌파선(직전 10봉 고점) 3% 이내이거나 오늘 거래대금(20일 평균 1.5배 이상)을 동반해 돌파, 손절선(10봉 저점)까지 8% 이내 · ④ 같은 업종에서는 시장 대비 가장 강한 하나만
              </p>
            </Section>

            {step === 4 ? (
              <Section title={`최종 후보 ${d.candidates.length}개`} subtitle="돈은 가장 좋은 몇 종목에만 - 지금 들고 있는 종목보다 나은 게 없으면 사지 않는다">
                {d.candidates.length === 0 ? <p className="text-sm text-muted-foreground">오늘은 네 단계를 모두 넘은 종목이 없습니다. 기다리는 것도 추세추종입니다.</p> : (
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{d.candidates.map((r) => <Candidate key={r.symbol} r={r} m={d.market} />)}</div>
                )}
              </Section>
            ) : (
              <Section title={`${"①②③"[step - 1]} ${d.funnel[step]?.label} 통과 ${rows.length}개`} subtitle="각 종목이 어느 단계에서 멈췄는지와 이유">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[860px] text-sm">
                    <thead><tr className="border-b text-left text-[11px] text-muted-foreground">
                      {["종목", "업종", "현재가", "거래대금", "시장 대비 60일", "하락일 낙폭", "타점", "결과"].map((h) => <th key={h} className="py-2 pr-3 font-normal">{h}</th>)}
                    </tr></thead>
                    <tbody>{rows.map((r) => (
                      <tr key={r.symbol} className="border-b last:border-0 align-top">
                        <td className="py-2 pr-3"><Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="font-medium hover:underline">{r.name}</Link><div className="text-[11px] text-muted-foreground">{r.symbol}</div></td>
                        <td className="pr-3 text-xs">{r.theme}</td>
                        <td className="pr-3 tabular">{px(r.close, d.market)} <span className={cn("text-xs", isNum(r.change_pct) && r.change_pct >= 0 ? "text-up" : "text-down")}>{pct(r.change_pct)}</span></td>
                        <td className="pr-3 tabular">{money(r.value, d.market)}</td>
                        <td className={cn("pr-3 tabular", (r.strength?.excess_60d ?? 0) > 0 ? "text-up" : "text-down")}>{r.strength ? `${r.strength.excess_60d >= 0 ? "+" : ""}${r.strength.excess_60d.toFixed(1)}%p` : "—"}</td>
                        <td className="pr-3 tabular">{downside(r.strength?.downside_ratio)}</td>
                        <td className="pr-3 text-xs tabular">{r.entry ? `${r.entry.state.startsWith("breakout") || r.entry.state === "extended" ? `돌파 +${r.entry.gap_pct.toFixed(1)}%` : `돌파선까지 ${r.entry.gap_pct.toFixed(1)}%`} · 손절폭 ${r.entry.risk_pct.toFixed(1)}%` : "—"}</td>
                        <td className="text-xs">{r.stage === 4 ? <span className="inline-flex items-center gap-1 font-semibold text-up"><CheckCircle2 className="size-3.5" />최종 후보</span>
                          : <span className="inline-flex items-start gap-1 text-muted-foreground"><XCircle className="mt-0.5 size-3.5 shrink-0" />{r.result}</span>}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </Section>
            )}

            <Section title="조건 하나만 못 넘은 종목" subtitle="[충]을 풀면 들어올 수 있는 종목 - 희소 가치는 숫자만으로 정해지지 않으니 직접 확인"
              right={<button onClick={() => setShowNear(!showNear)} className="inline-flex items-center gap-1 text-xs hover:text-foreground"><ChevronDown className={showNear ? "size-3.5 rotate-180" : "size-3.5"} />{showNear ? "접기" : `${d.near_miss.length}개 보기`}</button>}>
              {showNear ? (
                <ul className="grid gap-1 text-xs sm:grid-cols-2 xl:grid-cols-3">{d.near_miss.map((r) => (
                  <li key={r.symbol} className="flex items-center justify-between gap-2 rounded border px-2 py-1">
                    <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="truncate hover:underline">{r.name} <span className="text-muted-foreground">{r.theme}</span></Link>
                    <span className="shrink-0 text-down"><CircleDashed className="mr-1 inline size-3" />{condLabel(r.failed[0])}</span>
                  </li>))}</ul>
              ) : <p className="text-xs text-muted-foreground">조건 하나 차이로 빠진 종목 {d.near_miss.length}개 (거래대금 순).</p>}
            </Section>

            <Section title="매매 규칙과 하루 30분 루틴" subtitle="열 번 사면 두 번 번다 - 여섯 번은 본전, 두 번은 손절. 작게 잃거나 본전에서 나오기 때문에 계좌는 오른다">
              <div className="grid gap-4 md:grid-cols-2">
                <ol className="space-y-1.5 text-sm">
                  {(d.candidates[0]?.plan?.rules ?? ["손절은 최근 흔들림(10봉 저점) 아래 - 4~8% 안이어야 살 수 있는 자리", "+3% 오르면 손절선을 본전으로 올림",
                    "+25%에 닿으면 절반 매도, 나머지는 추세가 끝날 때까지 (예: 종가가 20일선 아래로 마감)", "돈은 가장 좋은 4~5종목에만 - 더 좋은 종목이 보이면 갈아탐"])
                    .map((r, i) => <li key={i} className="flex gap-2"><span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">{i + 1}</span>{r}</li>)}
                </ol>
                <div className="space-y-1.5 text-sm">
                  <div className="flex gap-2"><Timer className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><span><b>① 후보 고르기</b> - 이 화면의 4단계로 전체에서 몇 종목만 남긴다.</span></div>
                  <div className="flex gap-2"><Timer className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><span><b>② 차트 넘겨 보기</b> - 남은 종목을 일봉 → 30분봉 → 재무 → 수급 순서로 가볍게. 깊게가 아니라 매일 보는 게 중요하다.</span></div>
                  <div className="flex gap-2"><Timer className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><span><b>③ 비교하기</b> - 좋은 종목 ≠ 살 수 있는 종목. 손절 자리를 말할 수 없으면 사지 않는다.</span></div>
                  <p className="pt-1 text-[11px] text-muted-foreground">규칙을 그대로 계산한 결과이며 투자 권유가 아닙니다. 한국 업종은 KRX KIND 업종 분류를 테마로 씁니다.</p>
                </div>
              </div>
            </Section>
          </>
        )}
      </QueryState>
    </div>
  );
}
