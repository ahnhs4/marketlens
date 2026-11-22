"use client";

import { cn } from "cn";
import type { ReactNode } from "react";

import { Pill, Section } from "@/components/common";
import { frac, isNum, num } from "@/lib/format";

// Pieces shared by the fixed strategies (A–D), the strategy builder and the rotation backtest.

export interface Metrics {
  total_return: number; cagr: number; volatility: number; sharpe: number | null; sortino: number | null; max_drawdown: number;
  trades?: number; win_rate?: number | null; avg_win?: number | null; avg_loss?: number | null; profit_factor?: number | null;
  avg_holding_days?: number | null; exposure?: number | null; start: string; end: string;
}
export interface Trade { entry_date: string; exit_date: string; entry_price: number; exit_price: number; return: number; bars: number; open: boolean; reason?: string | null; reason_label?: string | null }
export type Curve = { date: string; value: number }[];
export type Tone = "good" | "bad" | "mixed" | "thin";
export interface RegimeBucket {
  label: string; days: number; share: number; return: number | null; annualized: number | null; hold_return: number | null; hold_annualized: number | null;
  trades: number; win_rate: number | null; avg_return: number | null; tone: Tone; verdict: string;
}
export interface RegimeDim {
  key: string; name: string; available: boolean; note?: string | null; coverage?: { from: string; to: string; share: number };
  current?: string | null; current_as_of?: string; current_tone?: Tone; current_verdict?: string; buckets?: RegimeBucket[];
}
export interface Regimes { dimensions: RegimeDim[]; tone: Tone; verdict: string; method: string }

export const COLORS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];
export const INPUT = "h-8 rounded-md border bg-background px-2 text-sm";
export const BTN = "inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60";
export const BTN_GHOST = "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground disabled:opacity-50";
export const usd = (v: number) => `$${Math.round(v).toLocaleString()}`;

const TONE: Record<Tone, { pill: "up" | "down" | "warn" | "default"; label: string }> = {
  good: { pill: "up", label: "과거 양호" }, bad: { pill: "down", label: "과거 부진" }, mixed: { pill: "warn", label: "엇갈림" }, thin: { pill: "default", label: "표본 부족" },
};
export function TonePill({ tone, children }: { tone?: Tone | null; children?: ReactNode }) {
  const t = TONE[tone ?? "thin"] ?? TONE.thin;
  return <Pill tone={t.pill}>{children ?? t.label}</Pill>;
}

export function MetricsGrid({ m }: { m: Metrics }) {
  const items: [string, string, number | null | undefined][] = [
    ["연평균 수익률 (CAGR)", frac(m.cagr), m.cagr], ["누적 수익률", frac(m.total_return), m.total_return], ["최대 낙폭 (MDD)", frac(m.max_drawdown), m.max_drawdown],
    ["샤프 지수", num(m.sharpe, 2), null], ["소르티노 지수", num(m.sortino, 2), null], ["변동성", frac(m.volatility), null],
    ["거래 횟수", String(m.trades ?? "—"), null], ["승률", frac(m.win_rate, 1, false), null], ["평균 수익 거래", frac(m.avg_win), m.avg_win],
    ["평균 손실 거래", frac(m.avg_loss), m.avg_loss], ["손익비 (Profit factor)", m.profit_factor === null || m.profit_factor === undefined ? "—" : Number.isFinite(m.profit_factor) ? m.profit_factor.toFixed(2) : "∞", null],
    ["평균 보유 기간", isNum(m.avg_holding_days) ? `${m.avg_holding_days.toFixed(0)}일` : "—", null], ["시장 노출 비중", frac(m.exposure, 0, false), null],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4 lg:grid-cols-7">
      {items.map(([k, v, s]) => (
        <div key={k}><dt className="text-[11px] text-muted-foreground">{k}</dt>
          <dd className={cn("text-sm font-semibold tabular", isNum(s) ? (s >= 0 ? "text-up" : "text-down") : "")}>{v}</dd></div>
      ))}
    </dl>
  );
}

export function BenchTable({ rows, highlight }: { rows: (readonly [string, Metrics | null | undefined])[]; highlight?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs tabular">
        <thead><tr className="text-left text-muted-foreground"><th className="font-normal">구분</th><th className="font-normal">CAGR</th><th className="font-normal">누적</th><th className="font-normal">MDD</th><th className="font-normal">샤프</th></tr></thead>
        <tbody>
          {rows.filter(([, m]) => m).map(([k, m]) => (
            <tr key={k} className={cn("border-t", k === highlight && "bg-primary/5")}><td className="py-1.5 pr-2 font-medium">{k}</td>
              <td className={m!.cagr >= 0 ? "text-up" : "text-down"}>{frac(m!.cagr)}</td><td>{frac(m!.total_return)}</td><td className="text-down">{frac(m!.max_drawdown)}</td><td>{num(m!.sharpe, 2)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TradesTable({ trades }: { trades: Trade[] }) {
  const reasons = trades.some((t) => t.reason_label);
  return (
    <div className="max-h-80 overflow-auto">
      <table className="w-full text-xs tabular">
        <thead className="sticky top-0 bg-card"><tr className="text-left text-muted-foreground"><th className="py-1 font-normal">진입일 (다음날 시가)</th><th className="font-normal">청산일</th><th className="font-normal">진입가</th><th className="font-normal">청산가</th><th className="font-normal">수익률</th><th className="font-normal">보유일</th>{reasons && <th className="font-normal">청산 사유</th>}</tr></thead>
        <tbody>{[...trades].reverse().map((t) => (
          <tr key={t.entry_date} className="border-t"><td className="py-1">{t.entry_date}</td><td>{t.exit_date}{t.open ? " (보유 중)" : ""}</td><td>{num(t.entry_price)}</td><td>{num(t.exit_price)}</td>
            <td className={t.return >= 0 ? "text-up" : "text-down"}>{frac(t.return)}</td><td>{t.bars}</td>{reasons && <td className="text-muted-foreground">{t.open ? "—" : t.reason_label ?? "—"}</td>}</tr>))}</tbody>
      </table>
    </div>
  );
}

/** 금리 · 공포/탐욕 · 매크로 국면별로 이 전략이 과거에 어땠는지, 그리고 지금 국면이 어디에 해당하는지. */
export function RegimePanel({ r }: { r?: Regimes | null }) {
  if (!r) return null;
  return (
    <Section title="국면별 성과 — 지금 이 전략을 써도 되나" subtitle={r.method}
      right={<TonePill tone={r.tone} />}>
      <p className={cn("rounded-lg border px-3.5 py-2.5 text-sm leading-relaxed",
        r.tone === "good" ? "border-up/25 bg-up/[0.05]" : r.tone === "bad" ? "border-down/25 bg-down/[0.05]" : r.tone === "mixed" ? "border-warn/30 bg-warn/[0.05]" : "bg-muted/20")}>{r.verdict}</p>
      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        {r.dimensions.map((d) => (
          <div key={d.key} className="min-w-0 rounded-lg border p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-[13px] font-semibold">{d.name}</h3>
              {d.available && d.current && <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">지금 <Pill tone="primary">{d.current}</Pill></span>}
            </div>
            {!d.available || !d.buckets ? <p className="mt-2 text-xs text-muted-foreground">데이터를 불러올 수 없습니다 (Data unavailable)</p> : (
              <>
                <p className="mt-1.5 text-xs leading-relaxed">{d.current_verdict} <TonePill tone={d.current_tone} /></p>
                <div className="mt-2.5 overflow-x-auto">
                  <table className="w-full text-[11px] tabular">
                    <thead><tr className="text-left text-muted-foreground"><th className="font-normal">국면</th><th className="font-normal">기간 비중</th><th className="font-normal">전략 (연율)</th><th className="font-normal">보유 (연율)</th><th className="font-normal">진입</th><th className="font-normal">승률</th><th className="font-normal">평균</th></tr></thead>
                    <tbody>{d.buckets.map((b) => (
                      <tr key={b.label} className={cn("border-t", b.label === d.current && "bg-primary/5")}>
                        <td className="py-1.5 pr-1 font-medium">{b.label}</td><td>{frac(b.share, 0, false)}</td>
                        <td className={isNum(b.annualized) ? (b.annualized >= 0 ? "text-up" : "text-down") : ""}>{frac(b.annualized)}</td>
                        <td className="text-muted-foreground">{frac(b.hold_annualized)}</td>
                        <td>{b.trades}</td><td>{frac(b.win_rate, 0, false)}</td>
                        <td className={isNum(b.avg_return) ? (b.avg_return >= 0 ? "text-up" : "text-down") : ""}>{frac(b.avg_return)}</td>
                      </tr>))}</tbody>
                  </table>
                </div>
                <p className="mt-2 text-[10.5px] leading-relaxed text-muted-foreground">{d.note}{d.coverage && d.coverage.share < 0.99 ? ` · 전체 기간의 ${frac(d.coverage.share, 0, false)}만 해당 (${d.coverage.from}부터)` : ""}</p>
              </>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}
