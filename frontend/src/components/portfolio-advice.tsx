"use client";

import { cn } from "cn";
import { ArrowRight, ChevronsDown, ChevronsUp, CircleHelp, Minus, MoveDown, MoveUp, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";

import { Pill } from "@/components/common";
import { AnalystPanel } from "@/components/analyst-panel";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { isNum, num, pct, price } from "@/lib/format";
import { signalKo } from "@/lib/i18n";

export type ActionKey = "STRONG_BUY" | "BUY" | "HOLD" | "TRIM" | "SELL" | "NA";

export interface Advice {
  position_id: number;
  symbol: string;
  name?: string | null;
  action: ActionKey;
  label: string;
  score: number | null;
  base_action?: ActionKey;
  summary: string;
  steps: { label: string; value: number | string; detail?: string | null }[];
  overrides: { rule: string; detail: string; from: string; to: string }[];
  positives: string[];
  negatives: string[];
  levels: { label: string; price: number; distance_pct: number; projected?: boolean }[];
  watch: string[];
  horizon_used?: "short" | "medium" | "long";
  horizons: Record<string, { score: number; view: string }>;
  rsi?: { short: number | null; medium: number | null; long: number | null; periods?: { short: number; medium: number; long: number } };
  scores: { overall?: number | null; technical?: number | null; macro?: number | null; fundamental?: number | null; timing?: number | null; signal?: string };
  position?: { price: number; avg_cost: number | null; unrealized_pl_pct: number | null; weight: number | null; weight_limit: number; max_drawdown: number;
    avg_cost_estimated?: boolean; since_ref_pct?: number | null };
  as_of?: string;
  method: string;
}

export interface AdviceResponse {
  items: Advice[];
  counts: Partial<Record<ActionKey, number>>;
  labels?: Record<ActionKey, string>;
  method: string;
  profile?: { holding_period?: string; risk_tolerance?: string; max_drawdown?: number };
  disclaimer?: string;
  updated_at: string;
}

export const ACTION_ORDER: ActionKey[] = ["STRONG_BUY", "BUY", "HOLD", "TRIM", "SELL", "NA"];
export const ACTION_LABEL: Record<ActionKey, string> = {
  STRONG_BUY: "강력 매수", BUY: "추가 매수", HOLD: "보유", TRIM: "비중 축소", SELL: "매도 추천", NA: "판단 보류",
};
// Icon + text always accompany the colour, so the label never depends on colour alone.
export const ACTION_STYLE: Record<ActionKey, { cls: string; Icon: typeof ChevronsUp }> = {
  STRONG_BUY: { cls: "bg-up/20 text-up border-up/40", Icon: ChevronsUp },
  BUY: { cls: "bg-up/10 text-up border-up/25", Icon: MoveUp },
  HOLD: { cls: "bg-muted text-foreground border-border", Icon: Minus },
  TRIM: { cls: "bg-warn/10 text-warn border-warn/30", Icon: MoveDown },
  SELL: { cls: "bg-down/15 text-down border-down/40", Icon: ChevronsDown },
  NA: { cls: "bg-muted text-muted-foreground border-dashed border-border", Icon: CircleHelp },
};
const HZ_KO: Record<string, string> = { short: "단기", medium: "중기", long: "장기" };
const VIEW_KO: Record<string, string> = { Bullish: "강세", Neutral: "중립", Bearish: "약세" };
const HOLD_KO: Record<string, string> = { Short: "단기", Medium: "중기", Long: "장기" };
const RISK_KO: Record<string, string> = { Conservative: "안정형", Moderate: "중립형", Aggressive: "공격형" };

export function ActionBadge({ action, size = "md" }: { action: ActionKey; size?: "sm" | "md" | "lg" }) {
  const st = ACTION_STYLE[action] ?? ACTION_STYLE.NA;
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border font-semibold", st.cls,
      size === "lg" ? "px-3 py-1 text-base" : size === "sm" ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5 text-xs")}>
      <st.Icon className={size === "lg" ? "size-4" : "size-3.5"} aria-hidden />
      {ACTION_LABEL[action]}
    </span>
  );
}

/** Counts per opinion with the tickers in each bucket; clicking a ticker opens its reasons. */
export function AdviceSummary({ data, onOpen }: { data: AdviceResponse; onOpen: (a: Advice) => void }) {
  const p = data.profile ?? {};
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
        {ACTION_ORDER.filter((k) => k !== "NA" || data.counts.NA).map((k) => {
          const items = data.items.filter((i) => i.action === k);
          return (
            <div key={k} className={cn("rounded-lg border p-3", items.length ? "bg-card" : "bg-card/50 opacity-70")}>
              <div className="flex items-center justify-between gap-2">
                <ActionBadge action={k} size="sm" />
                <span className="text-xl font-semibold tabular">{items.length}</span>
              </div>
              <div className="mt-2 flex min-h-6 flex-wrap gap-1">
                {items.length === 0 ? <span className="text-[11px] text-muted-foreground">해당 없음</span> : items.map((i) => (
                  <button key={i.position_id} onClick={() => onOpen(i)} className="rounded border px-1.5 py-0.5 font-mono text-[11px] hover:bg-accent"
                    title={`${i.name ?? i.symbol} - 사유 보기`}>{i.symbol.replace(/\.(KS|KQ)$/, "")}</button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[11px] text-muted-foreground">
        기준: 선호 보유기간 {HOLD_KO[p.holding_period ?? "Medium"] ?? p.holding_period} · 위험 성향 {RISK_KO[p.risk_tolerance ?? "Moderate"] ?? p.risk_tolerance}
        {isNum(p.max_drawdown) ? ` · 허용 낙폭 ${(p.max_drawdown * 100).toFixed(0)}%` : ""} (설정 &gt; 투자 성향에서 변경) · 종목을 누르면 사유가 나옵니다.
      </p>
    </div>
  );
}

function H({ children }: { children: React.ReactNode }) {
  return <h3 className="mb-1.5 text-xs font-semibold text-muted-foreground">{children}</h3>;
}

export function AdviceSheet({ advice, currency, onClose, disclaimer }: { advice: Advice | null; currency?: string | null; onClose: () => void; disclaimer?: string }) {
  // Keep the last holding on screen while the panel animates out, instead of an empty panel.
  const [last, setLast] = useState<Advice | null>(advice);
  if (advice && advice !== last) setLast(advice);
  const a = advice ?? last;
  const top = useRef<HTMLDivElement>(null);  // focus the header on open so the panel starts at the top
  return (
    <Sheet open={!!advice} onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent side="right" initialFocus={top} className="w-full overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        {a && (
          <>
            <SheetHeader ref={top} tabIndex={-1} className="border-b pb-3 outline-none">
              <SheetTitle className="flex flex-wrap items-center gap-2 pr-8">
                <span className="font-mono text-lg">{a.symbol}</span>
                <span className="truncate text-sm font-normal text-muted-foreground">{a.name}</span>
              </SheetTitle>
              <div className="flex flex-wrap items-center gap-3">
                <ActionBadge action={a.action} size="lg" />
                {isNum(a.score) && <span className="text-sm text-muted-foreground">판단 점수 <b className="text-xl text-foreground tabular">{a.score.toFixed(0)}</b>/100</span>}
                {a.base_action && a.base_action !== a.action && (
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">점수 기준 {ACTION_LABEL[a.base_action]}<ArrowRight className="size-3" />규칙 적용 후 {ACTION_LABEL[a.action]}</span>
                )}
              </div>
              <SheetDescription className="text-sm text-foreground">{a.summary}</SheetDescription>
            </SheetHeader>

            <div className="space-y-5 px-4 pb-6">
              {a.position && (
                <section>
                  <H>보유 현황</H>
                  <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                    {[["평균 단가", a.position.avg_cost_estimated ? <span key="ac" className="text-warn">미입력</span> : price(a.position.avg_cost, currency)], ["현재가", price(a.position.price, currency)],
                      ["평가 수익률", a.position.avg_cost_estimated ? <span key="pl" className="text-xs font-normal text-muted-foreground">평단 입력 후 계산</span>
                        : <span key="pl" className={isNum(a.position.unrealized_pl_pct) && a.position.unrealized_pl_pct >= 0 ? "text-up" : "text-down"}>{pct(a.position.unrealized_pl_pct)}</span>],
                      ["비중 (한도)", `${isNum(a.position.weight) ? a.position.weight.toFixed(1) : "—"}% (${a.position.weight_limit.toFixed(0)}%)`]].map(([k, v]) => (
                      <div key={String(k)} className="rounded-md border p-2"><dt className="text-muted-foreground">{k}</dt><dd className="mt-0.5 font-semibold tabular">{v}</dd></div>
                    ))}
                  </dl>
                </section>
              )}

              <section>
                <H>판단 과정</H>
                <ol className="space-y-1.5 text-sm">
                  {a.steps.map((s, i) => (
                    <li key={i} className="flex items-start justify-between gap-3 rounded-md border px-2.5 py-1.5">
                      <div className="min-w-0">
                        <div>{i + 1}. {s.label}</div>
                        {s.detail && <div className="text-[11px] text-muted-foreground">{s.detail}</div>}
                      </div>
                      <b className="shrink-0 tabular">{typeof s.value === "number" ? s.value.toFixed(1) : s.value}</b>
                    </li>
                  ))}
                </ol>
              </section>

              <section>
                <H>적용된 보유 규칙</H>
                {a.overrides.length === 0 ? <p className="text-xs text-muted-foreground">적용된 규칙 없음 - 점수 기준 의견을 그대로 사용</p> : (
                  <ul className="space-y-1.5 text-sm">{a.overrides.map((o, i) => (
                    <li key={i} className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-1.5">
                      <div className="flex flex-wrap items-center gap-1.5 text-xs font-medium"><ShieldAlert className="size-3.5 text-warn" />{o.rule}<span className="text-muted-foreground">{o.from}</span><ArrowRight className="size-3" />{o.to}</div>
                      <div className="mt-0.5 text-xs">{o.detail}</div>
                    </li>))}</ul>
                )}
              </section>

              <section>
                <H>기업 · 애널리스트 (마이크로)</H>
                <AnalystPanel symbol={a.symbol} compact />
              </section>

              <section className="grid gap-4 sm:grid-cols-2">
                <div>
                  <H>긍정 요인</H>
                  <ul className="space-y-1 text-xs">{a.positives.length ? a.positives.map((x, i) => <li key={i} className="flex gap-1.5"><MoveUp className="mt-0.5 size-3 shrink-0 text-up" />{x}</li>) : <li className="text-muted-foreground">없음</li>}</ul>
                </div>
                <div>
                  <H>부정 요인 / 위험</H>
                  <ul className="space-y-1 text-xs">{a.negatives.length ? a.negatives.map((x, i) => <li key={i} className="flex gap-1.5"><MoveDown className="mt-0.5 size-3 shrink-0 text-down" />{x}</li>) : <li className="text-muted-foreground">없음</li>}</ul>
                </div>
              </section>

              <section>
                <H>기간별 전망 · RSI</H>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  {(["short", "medium", "long"] as const).map((k) => {
                    const h = a.horizons[k];
                    const r = a.rsi?.[k];
                    const per = a.rsi?.periods?.[k];
                    return (
                      <div key={k} className={cn("rounded-md border p-2", a.horizon_used === k && "border-primary bg-primary/5")}>
                        <div className="flex items-center justify-between"><span className="font-medium">{HZ_KO[k]}</span>{a.horizon_used === k && <Pill tone="primary">판단 기준</Pill>}</div>
                        <div className="mt-1 text-lg font-semibold tabular">{h ? h.score.toFixed(0) : "—"} <span className="text-xs font-normal text-muted-foreground">{h ? VIEW_KO[h.view] ?? h.view : ""}</span></div>
                        <div className="text-muted-foreground">RSI{per ? `(${per})` : ""} <b className={cn("text-foreground", isNum(r) && r >= 70 && "text-down", isNum(r) && r <= 30 && "text-up")}>{num(r, 1)}</b></div>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  종합 {num(a.scores.overall, 0)} · 기술 {num(a.scores.technical, 0)} ({signalKo(a.scores.signal)}) · 진입 타이밍 {num(a.scores.timing, 0)} · 매크로 {num(a.scores.macro, 0)} · 펀더멘털 {num(a.scores.fundamental, 0)}
                </p>
              </section>

              {a.levels.length > 0 && (
                <section>
                  <H>참고 가격대</H>
                  <table className="w-full text-xs tabular">
                    <tbody>{a.levels.map((l) => (
                      <tr key={l.label} className="border-b last:border-0">
                        <td className="py-1.5 text-muted-foreground">{l.label}{l.projected ? " (추정)" : ""}</td>
                        <td className="py-1.5 text-right font-medium">{price(l.price, currency)}</td>
                        <td className={cn("w-20 py-1.5 text-right", l.distance_pct >= 0 ? "text-up" : "text-down")}>{l.distance_pct >= 0 ? "+" : ""}{l.distance_pct.toFixed(1)}%</td>
                      </tr>))}</tbody>
                  </table>
                </section>
              )}

              {a.watch.length > 0 && (
                <section>
                  <H>의견이 바뀌는 조건</H>
                  <ul className="space-y-1 text-xs">{a.watch.map((w, i) => <li key={i}>· {w}</li>)}</ul>
                </section>
              )}

              <Link href={`/stock/${encodeURIComponent(a.symbol)}`} className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-accent">
                종목 상세 분석 보기 <ArrowRight className="size-4" />
              </Link>

              <p className="border-t pt-3 text-[11px] text-muted-foreground">
                {a.method}.{a.as_of ? ` 기준일 ${a.as_of}.` : ""} {disclaimer ?? "규칙 기반 의견이며 투자 권유가 아닙니다."}
              </p>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
