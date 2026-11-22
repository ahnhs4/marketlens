"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronRight, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader, Pill, QueryState, Section, SkeletonBlock, Unavailable } from "@/components/common";
import { ActionBadge, type AdviceResponse, AdviceSheet, AdviceSummary } from "@/components/portfolio-advice";
import { BulkCostPanel, CostCell } from "@/components/portfolio-costs";
import { PortfolioTabs } from "@/components/portfolio-tabs";
import { api, del, post } from "@/lib/api";
import { compact, frac, isNum, num, pct, price, signClass, won } from "@/lib/format";
import { groupKo, levelKo } from "@/lib/i18n";

interface Pos { id: number; symbol: string; quantity: number; avg_cost: number; currency: string; account: string | null; price: number | null; name?: string; change_pct?: number | null;
  name_ko?: string | null; market_value?: number | null; cost?: number | null; unrealized_pl?: number | null; unrealized_pl_pct?: number | null; weight?: number | null; sector?: string | null; group?: string | null; country?: string; status?: string; delay_minutes?: number | null;
  avg_cost_estimated?: boolean; since_ref_pct?: number | null;
  // KRW view (backend krw_breakdown): FX P/L needs the KRW average cost from the broker app
  avg_cost_krw?: number | null; avg_fx?: number; fx_now?: number; ret_usd_pct?: number; ret_krw_pct?: number;
  value_krw?: number; cost_krw?: number; pl_krw?: number; price_pl_krw?: number; fx_pl_krw?: number }
interface Flag { risk: string; level: "High" | "Medium" | "Low"; value: string }
interface PF { id: number; name: string; base_currency: string; usdkrw: number | null; positions: Pos[]; message?: string;
  totals: { market_value: number; cost: number | null; unrealized_pl: number | null; unrealized_pl_pct: number | null;
    pl_basis_count: number; estimated_count: number; estimated_value: number;
    krw?: { value: number; cost: number; pl: number; ret_pct: number | null; price_pl: number; fx_pl: number; count: number } } | null;
  exposure?: Record<"sector" | "group" | "country" | "currency", Record<string, number>>;
  risk?: { volatility?: number; max_drawdown_1y?: number; beta?: number | null; crypto_correlation?: number; flags: Flag[]; correlation?: { assets: string[]; matrix: (number | null)[][] } } ; updated_at?: string }
interface Stress { scenario: Record<string, number | string>; positions: { symbol: string; weight: number; impact_pct: number | null; r2: number | null }[]; portfolio_impact_pct: number | null; coverage_pct: number; context_assets: { asset: string; proxy: string; impact_pct: number | null }[]; method: string }

function Bars({ data, label = (k: string) => k }: { data: Record<string, number>; label?: (k: string) => string }) {
  const max = Math.max(1, ...Object.values(data));
  return (
    <ul className="space-y-1">{Object.entries(data).map(([k, v]) => (
      <li key={k} className="grid grid-cols-[120px_1fr_44px] items-center gap-2 text-xs">
        <span className="truncate text-muted-foreground">{label(k)}</span>
        <div className="h-2 rounded-sm bg-muted"><div className="h-full rounded-sm bg-s1" style={{ width: `${(v / max) * 100}%` }} /></div>
        <span className="text-right tabular">{v.toFixed(1)}%</span>
      </li>))}</ul>
  );
}

export default function PortfolioPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => api<{ id: number; name: string; base_currency: string }[]>("/api/portfolios") });
  const pid = list.data?.[0]?.id;
  const pf = useQuery({ queryKey: ["portfolio", pid], enabled: !!pid, queryFn: () => api<PF>(`/api/portfolios/${pid}`), refetchInterval: 60_000 });
  const [form, setForm] = useState({ symbol: "", quantity: "", avg_cost: "", account: "" });
  const add = useMutation({
    mutationFn: () => post(`/api/portfolios/${pid}/positions`, { symbol: form.symbol, quantity: Number(form.quantity), avg_cost: Number(form.avg_cost), account: form.account || null }),
    onSuccess: () => { setForm({ symbol: "", quantity: "", avg_cost: "", account: "" }); qc.invalidateQueries({ queryKey: ["portfolio"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/portfolios/${pid}/positions/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio"] }) });
  // Opinions are heavier (full stock analysis per holding), so they load separately and refresh every 5 minutes.
  const posKey = (pf.data?.positions ?? []).map((x) => x.id).join(",");
  const advice = useQuery({ queryKey: ["portfolio-actions", pid, posKey], enabled: !!pid && posKey !== "",
    queryFn: () => api<AdviceResponse>(`/api/portfolios/${pid}/actions`), staleTime: 300_000, refetchInterval: 300_000 });
  const adviceOf = (id: number) => advice.data?.items.find((a) => a.position_id === id);
  const [openId, setOpenId] = useState<number | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const openAdvice = openId === null ? null : adviceOf(openId) ?? null;
  const scen = useQuery({ queryKey: ["scenarios"], queryFn: () => api<{ scenarios: Record<string, { label: string; market: number; rate_bp: number; usd: number; oil: number }> }>("/api/portfolio/scenarios"), staleTime: Infinity });
  const [custom, setCustom] = useState({ market: "-8", rate_bp: "50", usd: "3", oil: "15" });
  const stress = useMutation({ mutationFn: (body: Record<string, unknown>) => post<Stress>(`/api/portfolios/${pid}/stress`, body), onError: (e: Error) => toast.error(e.message) });
  const input = "h-8 rounded-md border bg-background px-2 text-sm";

  return (
    <div className="space-y-5">
      <PageHeader title="포트폴리오" description="보유 종목 · 손익 · 노출도 · 위험. 스트레스 테스트는 과거 실측 민감도를 쓰며, 예측이 아닙니다." />
      <PortfolioTabs />
      <Section title="보유 종목 추가">
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <label className="text-xs">종목<input required value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className={cn(input, "mt-1 block w-32 font-mono")} placeholder="AAPL / 005930" /></label>
          <label className="text-xs">수량<input required inputMode="decimal" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} className={cn(input, "mt-1 block w-24")} /></label>
          <label className="text-xs">평균 단가 (현지 통화)<input required inputMode="decimal" value={form.avg_cost} onChange={(e) => setForm({ ...form, avg_cost: e.target.value })} className={cn(input, "mt-1 block w-28")} /></label>
          <label className="text-xs">계좌<input value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value })} className={cn(input, "mt-1 block w-28")} placeholder="선택 사항" /></label>
          <button disabled={add.isPending || !pid} className="inline-flex h-8 items-center gap-1 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-60"><Plus className="size-4" />추가</button>
        </form>
      </Section>
      <QueryState q={pf} label="포트폴리오" skeleton={<SkeletonBlock rows={8} />}>
        {(p) => !p.totals ? <p className="text-sm text-muted-foreground">아직 보유 종목이 없습니다 - 위에서 추가하세요.</p> : (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
              {[["평가금액", `${p.base_currency === "USD" ? "$" : "₩"}${num(p.totals.market_value, 0)}`],
                [p.totals.estimated_count ? `매입금액 (평단 입력 ${p.totals.pl_basis_count}종목)` : "매입금액",
                  isNum(p.totals.cost) ? `${p.base_currency === "USD" ? "$" : "₩"}${num(p.totals.cost, 0)}` : <span key="c" className="text-sm text-warn">평단 미입력</span>],
                [p.totals.estimated_count ? `평가손익 (평단 입력 ${p.totals.pl_basis_count}종목)` : "평가손익",
                  isNum(p.totals.unrealized_pl) ? <span key="pl" className={p.totals.unrealized_pl >= 0 ? "text-up" : "text-down"}>{num(p.totals.unrealized_pl, 0)} ({pct(p.totals.unrealized_pl_pct)})</span>
                    : <span key="pl" className="text-sm text-warn">평단 입력 후 계산</span>],
                ...(p.totals.krw ? [[`원화 손익 (${p.totals.krw.count}종목)`,
                  <span key="k" className={p.totals.krw.pl >= 0 ? "text-up" : "text-down"}>{won(p.totals.krw.pl, true)} ({pct(p.totals.krw.ret_pct)})</span>,
                  <span key="ks" title="주가 손익 = 수량 × (현재가 − 평단) × 현재 환율 · 환차손익 = 달러 원금 × (현재 환율 − 매입 환율)">
                    주가 <b className={p.totals.krw.price_pl >= 0 ? "text-up" : "text-down"}>{won(p.totals.krw.price_pl, true)}</b>
                    {" · "}환율 <b className={p.totals.krw.fx_pl >= 0 ? "text-up" : "text-down"}>{won(p.totals.krw.fx_pl, true)}</b></span>]] : []),
                ["변동성 (1년)", frac(p.risk?.volatility, 1, false)], ["S&P 500 대비 베타", num(p.risk?.beta, 2)]].map(([k, v, sub]) => (
                <div key={String(k)} className="rounded-xl border bg-card p-3.5 shadow-card"><div className="text-[11px] text-muted-foreground">{k}</div><div className="mt-1 text-lg font-semibold tabular">{v}</div>
                  {sub && <div className="mt-0.5 text-[11px] text-muted-foreground tabular">{sub}</div>}</div>
              ))}
            </div>
            {p.totals.estimated_count > 0 && (
              <div className="space-y-2 rounded-lg border border-warn/40 bg-warn/5 p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span><b>평단 미입력 {p.totals.estimated_count}종목</b> - 잔고증명서에는 매입 단가가 없어 이 종목들의 손익 · 수익률은 계산하지 않습니다.
                    표의 &lsquo;기준일 대비&rsquo;는 가져온 날(10/7) 종가 이후의 변동일 뿐 실제 수익률이 아닙니다.</span>
                  <button onClick={() => setBulkOpen(!bulkOpen)} className="rounded-md border border-warn/50 px-2 py-1 text-xs font-medium hover:bg-warn/10">{bulkOpen ? "닫기" : "평단 한 번에 입력"}</button>
                </div>
                {bulkOpen && pid && <BulkCostPanel pid={pid} onDone={() => setBulkOpen(false)} />}
              </div>
            )}
            <Section title="보유 종목 투자 의견" subtitle="강력 매수 · 추가 매수 · 보유 · 비중 축소 · 매도 추천 - 종목을 누르면 사유가 나옵니다"
              right={advice.data && <span>{new Date(advice.data.updated_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 계산</span>}>
              {advice.isLoading ? <div className="space-y-2"><p className="text-xs text-muted-foreground">보유 종목별로 기술 · 매크로 · 펀더멘털 점수를 계산하는 중… (종목 수에 따라 수 초~수십 초)</p><SkeletonBlock rows={3} /></div>
                : advice.isError ? <Unavailable message={(advice.error as Error).message} onRetry={() => advice.refetch()} label="투자 의견" />
                  : advice.data && <AdviceSummary data={advice.data} onOpen={(a) => setOpenId(a.position_id)} />}
            </Section>
            <Section title="보유 종목" subtitle={`기준 통화 ${p.base_currency}${p.usdkrw ? ` · 원/달러 ${p.usdkrw.toFixed(1)}` : ""}`}>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1040px] text-sm tabular">
                  <thead><tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">{[["종목"], ["의견"], ["수량"], ["평균 단가", "달러 평단 아래 원화 평단 · 매입 환율"], ["현재가"], ["당일"], ["평가금액"], ["손익 (원)", "원화 평가손익 = 원화 평가금액 − 원화 매입금액"], ["수익률", "$ = 달러 기준 · ₩ = 원화 기준 (환율 변동 포함)"], ["환차손익", "달러 원금 × (현재 환율 − 매입 환율)"], ["비중"], ["섹터"], ["계좌"], [""]].map(([h, tip]) => <th key={h} title={tip} className="py-2 pr-3 font-normal">{h}</th>)}</tr></thead>
                  <tbody>{p.positions.map((r) => {
                    const adv = adviceOf(r.id);
                    return (
                    <tr key={r.id} className={cn("border-b last:border-0", adv && "cursor-pointer hover:bg-accent/40")} onClick={() => adv && setOpenId(r.id)}>
                      <td className="py-2 pr-3"><Link href={`/stock/${r.symbol}`} onClick={(e) => e.stopPropagation()} className="font-mono font-semibold hover:underline">{r.symbol}</Link><span className="block max-w-40 truncate text-[11px] text-muted-foreground">{r.name_ko ?? r.name}</span></td>
                      <td className="pr-3">
                        {adv ? (
                          <button onClick={(e) => { e.stopPropagation(); setOpenId(r.id); }} className="inline-flex items-center gap-1 rounded-md hover:opacity-80" aria-label={`${r.symbol} 의견 사유 보기`}>
                            <ActionBadge action={adv.action} size="sm" /><span className="text-[11px] text-muted-foreground">사유</span><ChevronRight className="size-3.5 text-muted-foreground" />
                          </button>
                        ) : advice.isLoading ? <span className="text-[11px] text-muted-foreground">분석 중…</span> : <span className="text-[11px] text-muted-foreground">—</span>}
                      </td>
                      <td className="pr-3">{Number.isInteger(r.quantity) ? r.quantity.toLocaleString("ko-KR") : r.quantity.toLocaleString("ko-KR", { maximumFractionDigits: 6 })}</td><td className="pr-3">{pid && <CostCell pid={pid} id={r.id} value={r.avg_cost} currency={r.currency} estimated={!!r.avg_cost_estimated} />}
                        {isNum(r.avg_cost_krw) && r.currency !== "KRW" && <span className="block text-[11px] text-muted-foreground" title="토스 앱의 원화 평균 단가 · 매입 환율">{won(r.avg_cost_krw)} · {num(r.avg_fx, 1)}</span>}</td>
                      <td className="pr-3">{r.price === null ? <span className="text-xs text-warn">데이터 없음 (Data unavailable)</span> : price(r.price, r.currency)}</td>
                      <td className={cn("pr-3", isNum(r.change_pct) && r.change_pct >= 0 ? "text-up" : "text-down")}>{pct(r.change_pct)}</td>
                      <td className="pr-3">{compact(r.market_value, p.base_currency)}</td>
                      <td className={cn("pr-3", signClass(r.pl_krw ?? r.unrealized_pl))}>{r.avg_cost_estimated ? <span className="text-muted-foreground">—</span>
                        : isNum(r.pl_krw) ? won(r.pl_krw, true) : compact(r.unrealized_pl, p.base_currency)}</td>
                      <td className="pr-3">
                        {r.avg_cost_estimated ? <span className="text-[11px] text-muted-foreground" title="가져온 날(10/7) 종가 대비 변동 - 실제 수익률 아님">기준일 대비 {pct(r.since_ref_pct)}</span>
                          : r.currency === "KRW" ? <span className={signClass(r.unrealized_pl_pct)}>{pct(r.unrealized_pl_pct)}</span>
                            : <><span className={cn("block", signClass(r.ret_usd_pct ?? r.unrealized_pl_pct))}><span className="text-[10px] text-muted-foreground">$ </span>{pct(r.ret_usd_pct ?? r.unrealized_pl_pct)}</span>
                              {isNum(r.ret_krw_pct) && <span className={cn("block text-xs", signClass(r.ret_krw_pct))}><span className="text-[10px] text-muted-foreground">₩ </span>{pct(r.ret_krw_pct)}</span>}</>}</td>
                      <td className="pr-3 text-xs">{r.currency === "KRW" ? <span className="text-muted-foreground" title="원화 종목은 환율 영향이 없습니다">—</span>
                        : isNum(r.fx_pl_krw) ? <span className={signClass(r.fx_pl_krw)} title={`매입 환율 ${num(r.avg_fx, 1)} → 현재 ${num(r.fx_now, 1)}`}>{won(r.fx_pl_krw, true)}</span>
                          : <span className="text-muted-foreground" title="원화 평단을 입력하면 계산됩니다">—</span>}</td>
                      <td className="pr-3">{isNum(r.weight) ? `${r.weight.toFixed(1)}%` : "—"}</td>
                      <td className="pr-3 text-xs">{r.group ? groupKo(r.group) : r.sector ? groupKo(r.sector) : "—"}</td><td className="pr-3 text-xs text-muted-foreground">{r.account ?? ""}</td>
                      <td><button onClick={(e) => { e.stopPropagation(); remove.mutate(r.id); }} className="rounded p-1 text-muted-foreground hover:bg-accent" aria-label={`${r.symbol} 삭제`}><Trash2 className="size-4" /></button></td>
                    </tr>);
                  })}</tbody>
                </table>
              </div>
            </Section>
            <div className="grid gap-4 lg:grid-cols-3">
              <Section title="포트폴리오 위험" subtitle="비중 · 1년 수익률 · 팩터 민감도로 계산한 경고">
                <ul className="space-y-2 text-sm">{p.risk?.flags.map((f) => (
                  <li key={f.risk} className="flex items-start justify-between gap-2">
                    <span>{f.risk}<span className="block text-xs text-muted-foreground">{f.value}</span></span>
                    <Pill tone={f.level === "High" ? "down" : f.level === "Medium" ? "warn" : "default"}>{levelKo(f.level)}</Pill>
                  </li>))}</ul>
                {isNum(p.risk?.max_drawdown_1y) && <p className="mt-2 text-xs text-muted-foreground">현재 비중 기준 최근 1년 최대 낙폭: {frac(p.risk!.max_drawdown_1y)}</p>}
              </Section>
              <Section title="노출도">
                <div className="space-y-3">
                  {p.exposure && (["group", "country", "currency"] as const).map((k) => <div key={k}><div className="mb-1 text-xs font-medium">{k === "group" ? "섹터 / 산업" : k === "country" ? "국가" : "통화"}</div><Bars data={p.exposure![k]} label={k === "currency" ? undefined : groupKo} /></div>)}
                </div>
              </Section>
              <Section title="상관관계 (1년 일간)">
                {p.risk?.correlation ? (
                  <table className="text-[11px] tabular"><thead><tr><th />{p.risk.correlation.assets.map((a) => <th key={a} className="px-1 font-normal text-muted-foreground">{a.replace(".KS", "")}</th>)}</tr></thead>
                    <tbody>{p.risk.correlation.assets.map((a, i) => (<tr key={a}><th className="pr-1 text-right font-normal text-muted-foreground">{a.replace(".KS", "")}</th>
                      {p.risk!.correlation!.matrix[i].map((v, j) => <td key={j} className="size-9 border border-card text-center" style={{ background: v === null ? undefined : `rgba(${v >= 0 ? "57,135,229" : "230,103,103"},${0.08 + Math.abs(v) * 0.7})` }}>{v === null ? "—" : v.toFixed(2)}</td>)}</tr>))}</tbody></table>
                ) : <p className="text-sm text-muted-foreground">가격 이력이 있는 보유 종목이 2개 이상 필요합니다.</p>}
              </Section>
            </div>
            <Section title="매크로 스트레스 테스트" subtitle="영향 = Σ 민감도 × 충격 (S&P 500 · 미 10년물 금리 · 달러 · 유가에 대한 3년 일간 회귀). 달러 기준 투자자의 원화 자산에는 환율 변동도 반영됩니다.">
              <div className="flex flex-wrap gap-1.5">
                {scen.data && Object.entries(scen.data.scenarios).map(([k, s]) => (
                  <button key={k} onClick={() => stress.mutate({ scenario: k })} className="rounded-md border px-2 py-1 text-xs hover:bg-accent">{s.label}</button>
                ))}
              </div>
              <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); stress.mutate({ market: Number(custom.market), rate_bp: Number(custom.rate_bp), usd: Number(custom.usd), oil: Number(custom.oil) }); }}>
                {([["market", "S&P 500 %"], ["rate_bp", "미 10년물 bp"], ["usd", "달러 %"], ["oil", "유가 %"]] as const).map(([k, l]) => (
                  <label key={k} className="text-xs">{l}<input value={custom[k]} onChange={(e) => setCustom({ ...custom, [k]: e.target.value })} className={cn(input, "mt-1 block w-20 tabular")} /></label>
                ))}
                <button className="h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground">사용자 시나리오 실행</button>
              </form>
              <div className="mt-4">
                {stress.isPending ? <SkeletonBlock rows={4} /> : stress.isError ? <Unavailable message={(stress.error as Error).message} /> : stress.data && (
                  <div className="grid gap-4 md:grid-cols-[220px_1fr_1fr]">
                    <div>
                      <div className="text-xs text-muted-foreground">{String(stress.data.scenario.label ?? "시나리오")}</div>
                      <div className={cn("text-3xl font-semibold tabular", isNum(stress.data.portfolio_impact_pct) && stress.data.portfolio_impact_pct >= 0 ? "text-up" : "text-down")}>{pct(stress.data.portfolio_impact_pct)}</div>
                      <div className="text-xs text-muted-foreground">포트폴리오 추정 영향 · 평가금액의 {stress.data.coverage_pct.toFixed(0)}% 반영</div>
                    </div>
                    <ul className="space-y-1 text-xs tabular">{stress.data.positions.map((r) => (
                      <li key={r.symbol} className="flex justify-between"><span className="font-mono">{r.symbol}</span><span className={isNum(r.impact_pct) && r.impact_pct >= 0 ? "text-up" : "text-down"}>{pct(r.impact_pct)}<span className="ml-2 text-muted-foreground">R² {num(r.r2, 2)}</span></span></li>))}</ul>
                    <ul className="space-y-1 text-xs tabular"><li className="font-medium">참고 자산</li>{stress.data.context_assets.map((r) => (
                      <li key={r.proxy} className="flex justify-between"><span>{r.asset} ({r.proxy})</span><span className={isNum(r.impact_pct) && r.impact_pct >= 0 ? "text-up" : "text-down"}>{pct(r.impact_pct)}</span></li>))}</ul>
                  </div>
                )}
              </div>
            </Section>
          </>
        )}
      </QueryState>
      <AdviceSheet advice={openAdvice} currency={pf.data?.positions.find((x) => x.id === openId)?.currency} onClose={() => setOpenId(null)} disclaimer={advice.data?.disclaimer} />
    </div>
  );
}
