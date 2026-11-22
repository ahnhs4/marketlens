"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDown, Landmark } from "lucide-react";
import { useState } from "react";

import { Pill, QueryState, Section, SkeletonBlock, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";

type Probs = { hike: number; hold: number; cut: number };
interface Range { lower: number; upper: number; steps: number; prob: number }
interface Meeting {
  date: string; days_to: number; rate_before: number; rate_after: number; change_bp: number; cum_change_bp: number;
  meeting: Probs; vs_today: Probs; ranges: Range[]; most_likely: Range | null;
  week_ago: { as_of: string; meeting: Probs; vs_today: Probs } | null;
  month_ago: { as_of: string; meeting: Probs; vs_today: Probs } | null;
}
export interface FedWatch {
  available: boolean; message?: string; as_of: string; target: { lower: number; upper: number }; effr: number;
  meetings: Meeting[]; next: Meeting; source: string; method: string; updated_at: string;
}

export const useFedWatch = () =>
  useQuery({ queryKey: ["fedwatch"], queryFn: () => api<FedWatch>("/api/macro/fedwatch"), staleTime: 600_000, refetchInterval: 900_000 });

const WD = ["일", "월", "화", "수", "목", "금", "토"];
const day = (iso: string) => { const d = new Date(`${iso}T12:00:00`); return `${d.getMonth() + 1}월 ${d.getDate()}일 (${WD[d.getDay()]})`; };
const short = (iso: string) => { const d = new Date(`${iso}T12:00:00`); return `${d.getMonth() + 1}/${d.getDate()}`; };
const rng = (r: { lower: number; upper: number }) => `${r.lower.toFixed(2)}–${r.upper.toFixed(2)}%`;
const p0 = (v: number) => `${v >= 99.95 || v < 0.05 ? v.toFixed(0) : v.toFixed(1)}%`;
const bp = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}bp`;

/** Which way the market leans at a meeting: the largest of cut / hold / hike. */
function lean(p: Probs) {
  const [k] = (Object.entries(p) as [keyof Probs, number][]).sort((a, b) => b[1] - a[1])[0];
  return { hike: "인상", hold: "동결", cut: "인하" }[k];
}

/** Stacked cut / hold / hike bar. Colors are policy colors, not up/down market colors. */
function ProbBar({ p, className, labels = false }: { p: Probs; className?: string; labels?: boolean }) {
  const seg = [
    { k: "cut", label: "인하", v: p.cut, c: "bg-s2 text-background" },
    { k: "hold", label: "동결", v: p.hold, c: "bg-foreground/20 text-foreground" },
    { k: "hike", label: "인상", v: p.hike, c: "bg-s8 text-background" },
  ].filter((s) => s.v > 0.05);
  return (
    <div className={cn("flex h-2 w-full overflow-hidden rounded-full bg-muted", className)} role="img"
      aria-label={`인하 ${p0(p.cut)} · 동결 ${p0(p.hold)} · 인상 ${p0(p.hike)}`}>
      {seg.map((s) => (
        <div key={s.k} className={cn("h-full first:rounded-l-full last:rounded-r-full", s.c, labels && "grid place-items-center text-[11px] font-semibold")}
          style={{ width: `${s.v}%` }}>
          {labels && s.v >= 12 ? `${s.label} ${p0(s.v)}` : null}
        </div>
      ))}
    </div>
  );
}

function Delta({ now, then, label }: { now: number; then: number | undefined; label: string }) {
  if (then === undefined) return <span className="text-muted-foreground">{label} —</span>;
  const d = now - then;
  return (
    <span>
      <span className="text-muted-foreground">{label}</span> <span className="tabular">{p0(then)}</span>
      <span className={cn("ml-1 tabular", Math.abs(d) < 0.5 ? "text-muted-foreground" : d > 0 ? "text-s8" : "text-s2")}>
        ({d > 0 ? "+" : ""}{d.toFixed(1)}%p)
      </span>
    </span>
  );
}

function NextMeeting({ d }: { d: FedWatch }) {
  const m = d.next;
  return (
    <div className="rounded-xl border border-gold/25 bg-gradient-to-br from-gold/[0.07] via-transparent to-transparent p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="text-[11px] font-medium tracking-[0.14em] text-gold">다음 FOMC</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-xl font-semibold">{day(m.date)}</span>
            <Pill tone="primary">D-{m.days_to}</Pill>
          </div>
        </div>
        <div className="text-right text-[13px]">
          <div className="text-muted-foreground">시장이 가장 크게 보는 결과</div>
          <div className="mt-0.5 font-semibold">
            {lean(m.meeting)} {p0(Math.max(m.meeting.hike, m.meeting.hold, m.meeting.cut))}
            {m.most_likely && <span className="ml-1.5 font-normal text-muted-foreground">→ {rng(m.most_likely)}</span>}
          </div>
        </div>
      </div>
      <ProbBar p={m.meeting} labels className="mt-3.5 h-7 rounded-lg" />
      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px]">
        <span className="tabular"><i className="mr-1 inline-block size-2 rounded-full bg-s2 align-middle" />인하 {p0(m.meeting.cut)}</span>
        <span className="tabular"><i className="mr-1 inline-block size-2 rounded-full bg-foreground/25 align-middle" />동결 {p0(m.meeting.hold)}</span>
        <span className="tabular"><i className="mr-1 inline-block size-2 rounded-full bg-s8 align-middle" />인상 {p0(m.meeting.hike)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t border-border/70 pt-3 text-[12.5px]">
        <span className="text-muted-foreground">인상 확률 변화</span>
        <Delta label="1주 전" now={m.meeting.hike} then={m.week_ago?.meeting.hike} />
        <Delta label="1달 전" now={m.meeting.hike} then={m.month_ago?.meeting.hike} />
        <span className="text-muted-foreground">· 내재 금리 {m.rate_before.toFixed(3)}% → {m.rate_after.toFixed(3)}% ({bp(m.change_bp)})</span>
      </div>
    </div>
  );
}

/** CME-style matrix: probability of each target range after every meeting. */
function RangeMatrix({ meetings, target }: { meetings: Meeting[]; target: FedWatch["target"] }) {
  const steps = [...new Set(meetings.flatMap((m) => m.ranges.filter((r) => r.prob >= 0.5).map((r) => r.steps)))].sort((a, b) => a - b);
  if (!steps.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-[12px] tabular">
        <thead>
          <tr className="text-muted-foreground">
            <th className="py-1.5 pr-3 text-left">회의</th>
            {steps.map((s) => (
              <th key={s} className={cn("px-1.5 py-1.5 text-right", s === 0 && "text-foreground")}>
                {rng({ lower: target.lower + s * 0.25, upper: target.lower + (s + 1) * 0.25 })}
                {s === 0 && <div className="text-[10px] font-normal text-gold">현재</div>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/60">
          {meetings.map((m) => (
            <tr key={m.date}>
              <td className="py-1.5 pr-3 text-muted-foreground">{short(m.date)}</td>
              {steps.map((s) => {
                const p = m.ranges.find((r) => r.steps === s)?.prob ?? 0;
                return (
                  <td key={s} className="px-1 py-1">
                    <div className={cn("rounded px-1.5 py-1 text-right", p < 0.5 && "text-muted-foreground/40")}
                      style={p >= 0.5 ? { background: `color-mix(in oklab, var(${s > 0 ? "--s8" : s < 0 ? "--s2" : "--gold"}) ${Math.round(6 + p * 0.4)}%, transparent)` } : undefined}>
                      {p >= 0.5 ? p0(p) : "·"}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Macro page: FOMC hike / hold / cut probabilities, CME FedWatch method. */
export function FedWatchCard() {
  const q = useFedWatch();
  const [view, setView] = useState<"list" | "matrix">("list");
  const [open, setOpen] = useState(false);
  return (
    <Section id="fedwatch"
      title={<span className="flex items-center gap-2"><Landmark className="size-4 text-gold" />FedWatch · FOMC 금리 확률</span>}
      subtitle={q.data?.available ? `현재 목표금리 ${rng(q.data.target)} · 실효금리(EFFR) ${q.data.effr.toFixed(2)}% · ${q.data.as_of} 연방기금 선물 종가 기준` : "연방기금 선물 가격에 반영된 FOMC 회의별 인상 · 동결 · 인하 확률"}
      right={<UpdatedAt at={q.data?.updated_at} source="CME FedWatch 방식" />}>
      <QueryState q={q} label="FedWatch" skeleton={<SkeletonBlock rows={7} />}>
        {(d) => !d.available ? <p className="text-sm text-muted-foreground">{d.message ?? "데이터를 불러올 수 없습니다 (Data unavailable)"}</p> : (
          <div className="space-y-5">
            <NextMeeting d={d} />

            <div>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-medium text-muted-foreground">회의별 전망 (현재 금리 대비 누적)</h3>
                <div className="inline-flex rounded-full border p-0.5 text-[11.5px]">
                  {(["list", "matrix"] as const).map((v) => (
                    <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
                      className={cn("rounded-full px-2.5 py-0.5", view === v ? "bg-gold/15 text-gold" : "text-muted-foreground hover:text-foreground")}>
                      {v === "list" ? "요약" : "금리 범위별 확률"}
                    </button>
                  ))}
                </div>
              </div>
              {view === "matrix" ? <RangeMatrix meetings={d.meetings} target={d.target} /> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-[12.5px] tabular">
                    <thead>
                      <tr className="text-left text-[11.5px] text-muted-foreground">
                        <th className="py-1.5 pr-3">회의</th>
                        <th className="py-1.5 pr-3 text-right">예상 금리</th>
                        <th className="py-1.5 pr-3 text-right">누적 변화</th>
                        <th className="py-1.5 pr-3">가장 유력한 범위</th>
                        <th className="w-[34%] py-1.5 pr-3">인하 · 동결 · 인상 (지금 대비)</th>
                        <th className="py-1.5 text-right">인상 · 1주 전</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {d.meetings.map((m) => (
                        <tr key={m.date}>
                          <td className="py-2 pr-3"><b className="font-medium">{short(m.date)}</b> <span className="text-[11px] text-muted-foreground">D-{m.days_to}</span></td>
                          <td className="py-2 pr-3 text-right">{m.rate_after.toFixed(3)}%</td>
                          <td className={cn("py-2 pr-3 text-right", m.cum_change_bp > 2 ? "text-s8" : m.cum_change_bp < -2 ? "text-s2" : "text-muted-foreground")}>{bp(m.cum_change_bp)}</td>
                          <td className="py-2 pr-3">{m.most_likely ? <>{rng(m.most_likely)} <span className="text-muted-foreground">{p0(m.most_likely.prob)}</span></> : "—"}</td>
                          <td className="py-2 pr-3">
                            <ProbBar p={m.vs_today} />
                            <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
                              <span>인하 {p0(m.vs_today.cut)}</span><span>동결 {p0(m.vs_today.hold)}</span><span className="text-foreground/80">인상 {p0(m.vs_today.hike)}</span>
                            </div>
                          </td>
                          <td className="py-2 text-right">
                            {m.vs_today.hike.toFixed(1)}%
                            {m.week_ago && <div className="text-[11px] text-muted-foreground">{m.week_ago.vs_today.hike.toFixed(1)}%</div>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" aria-expanded={open}>
              <ChevronDown className={open ? "size-3.5 rotate-180" : "size-3.5"} />계산 방법 · 출처
            </button>
            {open && (
              <div className="space-y-1.5 text-[11.5px] leading-5 text-muted-foreground">
                <p>출처: {d.source}</p>
                <p className="whitespace-pre-line">{d.method}</p>
                <p>CME 공식 페이지는 정산가를 쓰기 때문에 소수점 차이가 날 수 있습니다. 확률은 시장 가격에 반영된 기대이며 예측이 아닙니다.</p>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Section>
  );
}

/** Dashboard tile body: next meeting and its odds. */
export function FedWatchTile() {
  const q = useFedWatch();
  if (q.isLoading) return <SkeletonBlock rows={2} />;
  const d = q.data;
  if (!d?.available) return <span className="text-xs text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable)</span>;
  const m = d.next;
  return (
    <div>
      <div className="text-[12px] text-muted-foreground">{short(m.date)} 회의 · D-{m.days_to} · FedWatch</div>
      <div className="mt-0.5 flex flex-wrap gap-x-2 text-[13px] tabular">
        <span className="text-s8">인상 {p0(m.meeting.hike)}</span>
        <span>동결 {p0(m.meeting.hold)}</span>
        {m.meeting.cut >= 0.5 && <span className="text-s2">인하 {p0(m.meeting.cut)}</span>}
      </div>
      <ProbBar p={m.meeting} className="mt-1.5 h-1.5" />
    </div>
  );
}
