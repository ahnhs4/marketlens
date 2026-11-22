"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { NotebookPen, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Pill, QueryState, Section, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { currencySymbol, frac, isNum, num, price, won } from "@/lib/format";
import { STATIC } from "@/lib/static";
import { useStored } from "@/lib/stored";

// Trade plan: entry / stop / targets / reward-to-risk and the position size for the user's account,
// each price with the indicator it comes from. A calculation only - nothing here places an order.

interface Target { label: string; price: number; kind: string; basis: string; projected: boolean; pct: number; rr: number | null }
interface Plan {
  symbol: string; currency: string; fx_usdkrw: number | null; as_of: string; updated_at: string;
  entry: { price: number; basis: string; detail: string };
  stop: { price: number; kind: "support" | "atr"; basis: string; detail: string; pct: number; atr_multiple: number | null };
  targets: Target[]; reward_risk: number | null; min_reward_risk: number;
  sizing: {
    account_krw: number; risk_pct: number; risk_budget_krw: number; max_weight_pct: number; per_share_risk: number; per_share_risk_krw: number;
    quantity: number; quantity_by_risk: number; quantity_by_weight: number; capped_by_weight: boolean; position_value_krw: number; weight_pct: number;
    loss_if_stopped_krw: number; formula: string;
  };
  pullback: { entry: number; basis: string; distance_pct: number; rr: number | null; quantity: number } | null;
  warnings: string[]; indicators: { atr: number | null; atr_pct: number | null; rsi: number | null; stoch_medium: number | null; stoch_long: number | null };
  disclaimer: string;
}
interface Prefs { account_man: string; risk_pct: string; max_weight_pct: string }
const DEFAULTS: Prefs = { account_man: "1000", risk_pct: "1", max_weight_pct: "25" };
const FIELD = "h-8 w-full rounded-md border bg-background px-2 text-sm tabular";
const clamp = (v: string, lo: number, hi: number, fallback: number) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? Math.min(hi, Math.max(lo, n)) : fallback; };

function Tile({ label, value, sub, tone, basis, detail }: { label: string; value: string; sub?: string; tone: "up" | "down" | "base"; basis: string; detail?: string }) {
  return (
    <div className={cn("min-w-0 rounded-lg border px-3 py-2.5", tone === "up" ? "border-up/25 bg-up/[0.04]" : tone === "down" ? "border-down/25 bg-down/[0.04]" : "border-gold/30 bg-gold/[0.04]")}>
      <div className="flex items-baseline justify-between gap-2 text-[11px] text-muted-foreground"><span>{label}</span>
        {sub && <span className={cn("tabular font-medium", tone === "up" ? "text-up" : tone === "down" ? "text-down" : "")}>{sub}</span>}</div>
      <div className="mt-0.5 text-xl font-semibold tabular">{value}</div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground" title={detail}><span className="text-foreground/80">근거</span> · {basis}</p>
    </div>
  );
}

function Inputs({ prefs, onApply }: { prefs: Prefs; onApply: (p: Prefs) => void }) {
  const [d, setD] = useState(prefs);
  const dirty = d.account_man !== prefs.account_man || d.risk_pct !== prefs.risk_pct || d.max_weight_pct !== prefs.max_weight_pct;
  const field = (k: keyof Prefs, label: string, unit: string) => (
    <label className="min-w-0 text-[11px] text-muted-foreground">{label}
      <span className="mt-1 flex items-center gap-1"><input inputMode="decimal" value={d[k]} onChange={(e) => setD({ ...d, [k]: e.target.value })}
        onKeyDown={(e) => { if (e.key === "Enter") onApply(d); }} className={FIELD} /><span className="shrink-0">{unit}</span></span>
    </label>
  );
  return (
    <div className="mt-3 border-t pt-3">
      <div className="grid grid-cols-3 gap-2">{field("account_man", "내 계좌", "만원")}{field("risk_pct", "1회 손실 한도", "%")}{field("max_weight_pct", "종목당 최대", "%")}</div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <p className="text-[10.5px] leading-relaxed text-muted-foreground">이 브라우저에만 저장됩니다.</p>
        <button onClick={() => onApply(d)} disabled={!dirty} className="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-accent/50 disabled:opacity-40">다시 계산</button>
      </div>
    </div>
  );
}

export function TradePlan({ symbol }: { symbol: string }) {
  const [prefs, setPrefs] = useStored<Prefs>("ml:plan", DEFAULTS);
  const account = clamp(prefs.account_man, 10, 1e8, 1000) * 10_000;
  const risk = clamp(prefs.risk_pct, 0.1, 10, 1);
  const weight = clamp(prefs.max_weight_pct, 1, 100, 25);
  // The public demo only carries the example account (1,000만원 · 1%).
  const path = `/api/stock/${encodeURIComponent(symbol)}/plan${STATIC ? "" : `?account_krw=${account}&risk_pct=${risk}&max_weight_pct=${weight}`}`;
  const q = useQuery({ queryKey: ["plan", path], queryFn: () => api<Plan>(path), staleTime: 120_000 });

  return (
    <Section id="plan" title="매매 계획" subtitle="매수가 · 손절가 · 목표가 · 손익비 · 내 계좌 기준 적정 수량 — 각 가격의 근거와 함께" right={<UpdatedAt at={q.data?.updated_at} />}>
      <QueryState q={q} label="매매 계획">
        {(p) => {
          const c = p.currency;
          const px = (v: number) => `${currencySymbol(c)}${price(v, c)}`;
          const t1 = p.targets[0];
          const riskAbs = p.entry.price - p.stop.price;
          const rewardAbs = t1 ? t1.price - p.entry.price : 0;
          const riskShare = riskAbs + rewardAbs > 0 ? Math.min(92, Math.max(8, (riskAbs / (riskAbs + rewardAbs)) * 100)) : 50;
          const ok = isNum(p.reward_risk) && p.reward_risk >= p.min_reward_risk;
          const s = p.sizing;
          return (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
              <div className="min-w-0 space-y-3">
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                  <Tile label="손절가" tone="down" value={px(p.stop.price)} sub={frac(p.stop.pct)} basis={p.stop.basis} detail={p.stop.detail} />
                  <Tile label="매수가" tone="base" value={px(p.entry.price)} sub={p.as_of.slice(5).replace("-", ".")} basis={p.entry.basis} detail={p.entry.detail} />
                  {p.targets.slice(0, 2).map((t, i) => (
                    <Tile key={t.label} label={`${i + 1}차 목표가`} tone="up" value={px(t.price)} sub={`${frac(t.pct)}${isNum(t.rr) ? ` · ${t.rr.toFixed(1)}R` : ""}`}
                      basis={`${t.basis}${t.projected ? " (저항선이 없어 손절폭의 배수로 계산)" : ""}`} />
                  ))}
                  {p.targets.length === 0 && <div className="rounded-lg border px-3 py-2.5 text-xs text-muted-foreground sm:col-span-2">목표가를 계산할 저항선이 없습니다 (Data unavailable)</div>}
                </div>

                <div className="rounded-lg border px-3.5 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold">손익비 <span className={cn("ml-1 text-lg tabular", ok ? "text-up" : "text-warn")}>{isNum(p.reward_risk) ? `${p.reward_risk.toFixed(2)} : 1` : "—"}</span></span>
                    <Pill tone={ok ? "up" : "warn"}>{ok ? `기준 ${p.min_reward_risk} : 1 이상` : `기준 ${p.min_reward_risk} : 1 미달`}</Pill>
                  </div>
                  {t1 && (
                    <>
                      <div className="mt-2.5 flex h-2.5 overflow-hidden rounded-full" role="img" aria-label={`위험 ${frac(p.stop.pct)} 대비 보상 ${frac(t1.pct)}`}>
                        <span className="bg-down-mark/80" style={{ width: `${riskShare}%` }} /><span className="w-0.5 bg-foreground" /><span className="flex-1 bg-up-mark/80" />
                      </div>
                      <div className="mt-1.5 flex justify-between text-[11px] tabular text-muted-foreground">
                        <span>손절까지 <b className="text-down">{frac(p.stop.pct)}</b>{isNum(p.stop.atr_multiple) ? ` · ATR ${p.stop.atr_multiple.toFixed(1)}배` : ""}</span>
                        <span>1차 목표까지 <b className="text-up">{frac(t1.pct)}</b></span>
                      </div>
                    </>
                  )}
                  {p.pullback && (
                    <p className="mt-2.5 border-t pt-2.5 text-xs leading-relaxed">
                      <span className="font-medium">눌림목 대안</span> · {p.pullback.basis} <b className="tabular">{px(p.pullback.entry)}</b> ({frac(p.pullback.distance_pct)})
                      {isNum(p.pullback.rr) && <> → 손익비 <b className={cn("tabular", p.pullback.rr >= p.min_reward_risk ? "text-up" : "")}>{p.pullback.rr.toFixed(2)} : 1</b></>} · 수량 {p.pullback.quantity.toLocaleString()}주
                    </p>
                  )}
                </div>

                {p.warnings.length > 0 && (
                  <ul className="space-y-1.5">
                    {p.warnings.map((w) => (
                      <li key={w} className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/[0.05] px-3 py-2 text-xs leading-relaxed"><TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />{w}</li>
                    ))}
                  </ul>
                )}
                <p className="text-[11px] tabular text-muted-foreground">
                  ATR {num(p.indicators.atr, 2)} ({num(p.indicators.atr_pct, 1)}%) · RSI {num(p.indicators.rsi, 1)} · 스토캐스틱 중기 {num(p.indicators.stoch_medium, 1)} / 장기 {num(p.indicators.stoch_long, 1)}
                  {c !== "KRW" && isNum(p.fx_usdkrw) ? ` · 환율 ${num(p.fx_usdkrw, 1)}원` : ""}
                </p>
              </div>

              <div className="min-w-0 rounded-lg border p-3.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] text-muted-foreground">적정 수량</span>
                  {s.capped_by_weight && <Pill tone="warn">비중 한도로 축소</Pill>}
                </div>
                <div className="mt-0.5 text-3xl font-semibold tabular">{s.quantity.toLocaleString()}<span className="ml-1 text-base font-medium text-muted-foreground">주</span></div>
                <dl className="mt-3 space-y-1.5 text-xs tabular">
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">매수 금액</dt><dd>{won(s.position_value_krw)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">계좌 비중</dt><dd>{num(s.weight_pct, 1)}% <span className="text-muted-foreground">/ 한도 {num(s.max_weight_pct, 0)}%</span></dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">손절 시 손실</dt><dd className="text-down">-{won(s.loss_if_stopped_krw)}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-muted-foreground">1회 손실 한도</dt><dd>{won(s.risk_budget_krw)} <span className="text-muted-foreground">({num(s.risk_pct, 1)}%)</span></dd></div>
                </dl>
                <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">{s.formula}</p>
                {STATIC ? <p className="mt-3 border-t pt-3 text-[11px] leading-relaxed text-muted-foreground">데모는 예시 계좌 {won(s.account_krw)} · 1회 손실 한도 {num(s.risk_pct, 0)}% 기준입니다. 실제 앱에서는 내 계좌 크기로 바꿔 계산합니다.</p>
                  : <Inputs key={`${prefs.account_man}|${prefs.risk_pct}|${prefs.max_weight_pct}`} prefs={prefs} onApply={setPrefs} />}
                {!STATIC && s.quantity > 0 && (
                  <Link href={`/journal?symbol=${encodeURIComponent(p.symbol)}&entry=${p.entry.price.toFixed(c === "KRW" ? 0 : 2)}&stop=${p.stop.price}&qty=${s.quantity}${t1 ? `&target=${t1.price}` : ""}`}
                    className="mt-3 inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"><NotebookPen className="size-3.5" />이 계획으로 매매 일지 쓰기</Link>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground xl:col-span-2">{p.disclaimer}</p>
            </div>
          );
        }}
      </QueryState>
    </Section>
  );
}
