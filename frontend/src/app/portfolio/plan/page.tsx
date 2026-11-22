"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowDownRight, ArrowUpRight, Info } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock } from "@/components/common";
import { PortfolioTabs } from "@/components/portfolio-tabs";
import { api, post, put } from "@/lib/api";
import { isNum, pct, price, signClass, won } from "@/lib/format";

type Bucket = "lev" | "dividend" | "index" | "theme" | "stock";
interface PlanPrefs { dividend_target_krw?: number | null; dividend_basis?: "net" | "gross"; realized_overseas_krw?: number;
  contribution_krw?: number; targets?: Partial<Record<Bucket, number>> | null; bucket_overrides?: Record<string, Bucket | null> }
interface Holding { id: number; symbol: string; name: string | null; currency: string; price: number; quantity: number; value_krw: number;
  pl_krw: number | null; bucket: Bucket; bucket_auto: Bucket; bucket_reason: string; overridden: boolean; yield: number | null;
  growth_5y: number | null; raise_streak: number | null; div_krw: number; div_net_krw: number; div_irregular_krw: number;
  action: string | null; label: string | null; score: number | null; avg_cost_estimated: boolean }
interface Trade { side: "sell" | "buy"; symbol: string; name: string | null; bucket: Bucket; currency: string; price: number; shares: number;
  amount_krw: number; all?: boolean; held?: boolean; realized_krw?: number | null; transaction_tax_krw?: number; div_change_krw: number;
  div_net_change_krw: number; score: number | null; label: string | null; reason: string }
interface Plan {
  message?: string; fx_usdkrw: number; total_krw: number; contribution_krw: number;
  buckets: { key: Bucket; label: string; current_krw: number; current_pct: number; target_pct: number | null; after_krw: number; after_pct: number; count: number }[];
  holdings: Holding[];
  dividend: { gross_krw: number; net_krw: number; basis: "net" | "gross"; target_krw: number | null; gap_krw: number | null; progress_pct: number | null;
    need: { symbol: string; name: string; yield: number; amount_krw: number; bucket_pct: number | null } | null;
    after_gross_krw: number; after_net_krw: number; after_progress_pct: number | null; irregular_krw: number;
    irregular: { symbol: string; name: string; amount_krw: number; reason: string }[]; top: { symbol: string; name: string; div_krw: number; yield: number }[] };
  tax: { realized_overseas_krw: number; overseas_now_krw: number; planned_realized_krw: number; planned_unknown: string[];
    overseas_with_plan_krw: number; transaction_with_plan_krw: number; if_all_sold_gain_krw: number; overseas_if_all_sold_krw: number;
    harvestable_loss_krw: number; harvest_saving_krw: number; dividend_withheld_krw: Record<string, number>; dividend_withheld_total_krw: number;
    financial_income_warning: boolean; total_with_plan_krw: number; total_now_krw: number; notes: string[] };
  trades: Trade[]; notes: string[]; plan: PlanPrefs; disclaimer: string; updated_at: string;
}

const ORDER: Bucket[] = ["lev", "dividend", "index", "theme", "stock"];
const LABEL: Record<Bucket, string> = { lev: "레버리지·인버스 ETF", dividend: "배당·배당성장", index: "지수 ETF", theme: "테마·섹터 ETF", stock: "개별주" };
const COLOR: Record<Bucket, string> = { lev: "bg-down", dividend: "bg-up", index: "bg-s1", theme: "bg-warn", stock: "bg-primary" };
const toNum = (t: string) => { const v = Number(t.replace(/[,원₩\s]/g, "")); return t.trim() === "" || !Number.isFinite(v) ? null : v; };
const fmtIn = (v: number | null | undefined) => (isNum(v) ? Math.round(v).toLocaleString("ko-KR") : "");

interface Draft { target: string; basis: "net" | "gross"; realized: string; contribution: string; targets: Record<Bucket, string> }

function draftFrom(p: Plan): Draft {
  const t = p.plan.targets;
  return {
    target: fmtIn(p.plan.dividend_target_krw), basis: p.plan.dividend_basis ?? "net", realized: fmtIn(p.plan.realized_overseas_krw),
    contribution: fmtIn(p.plan.contribution_krw),
    targets: Object.fromEntries(ORDER.map((b) => [b, t ? String(t[b] ?? 0) : String(Math.round(p.buckets.find((x) => x.key === b)?.current_pct ?? 0))])) as Record<Bucket, string>,
  };
}

function prefsFrom(d: Draft): PlanPrefs {
  return { dividend_target_krw: toNum(d.target), dividend_basis: d.basis, realized_overseas_krw: toNum(d.realized) ?? 0,
    contribution_krw: toNum(d.contribution) ?? 0, targets: Object.fromEntries(ORDER.map((b) => [b, toNum(d.targets[b]) ?? 0])) };
}

export default function PlanPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["portfolios"], queryFn: () => api<{ id: number }[]>("/api/portfolios") });
  const pid = list.data?.[0]?.id;
  const [preview, setPreview] = useState<PlanPrefs | null>(null);
  const plan = useQuery({
    queryKey: ["plan", pid, preview ? JSON.stringify(preview) : "saved"], enabled: !!pid, staleTime: 300_000,
    queryFn: () => (preview ? post<Plan>(`/api/portfolios/${pid}/plan/preview`, preview) : api<Plan>(`/api/portfolios/${pid}/plan`)),
    placeholderData: (prev) => prev,
  });
  const save = useMutation({
    mutationFn: (p: PlanPrefs) => put("/api/settings", { preferences: { plan: p } }),
    onSuccess: () => { toast.success("목표를 저장했습니다"); setPreview(null); qc.invalidateQueries({ queryKey: ["plan"] }); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const setOverride = useMutation({
    mutationFn: ({ symbol, bucket }: { symbol: string; bucket: Bucket | null }) => put("/api/settings", { preferences: { plan: { bucket_overrides: { [symbol]: bucket } } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plan"] }),
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-5">
      <PageHeader title="배당 · 세금 · 리밸런싱" description="연간 배당 목표 · 올해 낼 세금 추정 · 목표 비중에 맞추는 매매 제안. 숫자는 실제 시세와 배당 이력으로 계산합니다." />
      <PortfolioTabs />
      {plan.isLoading && <p className="text-xs text-muted-foreground">보유 종목별 배당 이력과 판단 점수를 계산하는 중… 처음에는 1분 넘게 걸릴 수 있습니다.</p>}
      <QueryState q={plan} label="배당 · 세금 · 리밸런싱" skeleton={<SkeletonBlock rows={10} />}>
        {(p) => p.message ? <p className="text-sm text-muted-foreground">{p.message}</p> : (
          <>
            <Settings key={JSON.stringify(p.plan) + (preview ? "p" : "s")} p={p} previewing={!!preview} busy={plan.isFetching}
              onPreview={(d) => setPreview(prefsFrom(d))} onSave={(d) => save.mutate(prefsFrom(d))} onReset={() => setPreview(null)} saving={save.isPending} />
            <div className="grid gap-4 xl:grid-cols-2">
              <DividendCard p={p} />
              <TaxCard p={p} />
            </div>
            <AllocationCard p={p} />
            <TradesCard p={p} />
            <HoldingsCard p={p} onOverride={(symbol, bucket) => setOverride.mutate({ symbol, bucket })} />
            <p className="text-[11px] text-muted-foreground">{p.disclaimer} 원/달러 {p.fx_usdkrw?.toFixed(1)} 기준 · 판단 점수는 <Link href="/portfolio" className="underline">보유 현황</Link>의 투자 의견과 같은 값입니다.</p>
          </>
        )}
      </QueryState>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="block text-xs"><span className="font-medium">{label}</span>{children}{hint && <span className="mt-0.5 block text-[11px] text-muted-foreground">{hint}</span>}</label>;
}

function Settings({ p, previewing, busy, saving, onPreview, onSave, onReset }: { p: Plan; previewing: boolean; busy: boolean; saving: boolean;
  onPreview: (d: Draft) => void; onSave: (d: Draft) => void; onReset: () => void }) {
  const [d, setD] = useState<Draft>(() => draftFrom(p));
  const sum = ORDER.reduce((s, b) => s + (toNum(d.targets[b]) ?? 0), 0);
  const input = "mt-1 h-8 w-full rounded-md border bg-background px-2 text-sm tabular";
  return (
    <Section title="목표 설정" subtitle="바꾼 뒤 '미리보기'로 결과를 보고, 마음에 들면 저장하세요"
      right={previewing && <Pill tone="warn">저장 안 된 미리보기</Pill>}>
      <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        <div className="space-y-3">
          <Field label="받고 싶은 연간 배당금 (원)" hint={isNum(toNum(d.target)) ? `월 ${won((toNum(d.target) ?? 0) / 12)} 꼴` : "비워 두면 목표 없이 현재 배당만 보여 줍니다"}>
            <div className="flex gap-2">
              <input value={d.target} onChange={(e) => setD({ ...d, target: e.target.value })} inputMode="numeric" placeholder="예: 3,000,000" className={input} />
              <div className="mt-1 shrink-0"><Segmented size="xs" ariaLabel="배당 기준" value={d.basis} onChange={(v) => setD({ ...d, basis: v })}
                options={[{ value: "net", label: "세후" }, { value: "gross", label: "세전" }]} /></div>
            </div>
          </Field>
          <Field label="올해 해외주식 실현 손익 (원)" hint="1월 1일 이후 이미 판 해외 종목의 손익 합계 (토스증권 > 판매수익). 손실이면 앞에 −">
            <input value={d.realized} onChange={(e) => setD({ ...d, realized: e.target.value })} inputMode="numeric" placeholder="0" className={input} />
          </Field>
          <Field label="추가로 넣을 돈 (원, 선택)" hint="리밸런싱 때 매도 대금과 함께 매수에 씁니다">
            <input value={d.contribution} onChange={(e) => setD({ ...d, contribution: e.target.value })} inputMode="numeric" placeholder="0" className={input} />
          </Field>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between text-xs"><span className="font-medium">목표 자산 비중 (%)</span>
            <span className={cn("tabular", Math.abs(sum - 100) > 0.05 ? "text-warn" : "text-muted-foreground")}>합계 {sum.toFixed(0)}%{Math.abs(sum - 100) > 0.05 ? " - 100%로 비례 조정해 계산" : ""}</span></div>
          <ul className="space-y-1.5">
            {ORDER.map((b) => {
              const cur = p.buckets.find((x) => x.key === b);
              return (
                <li key={b} className="grid grid-cols-[1fr_72px_88px] items-center gap-2 text-sm">
                  <span className="flex items-center gap-2"><span className={cn("size-2.5 rounded-sm", COLOR[b])} />{LABEL[b]} <span className="text-[11px] text-muted-foreground">{cur?.count ?? 0}종목</span></span>
                  <span className="text-right text-xs tabular text-muted-foreground">현재 {cur?.current_pct.toFixed(1)}%</span>
                  <input aria-label={`${LABEL[b]} 목표 비중`} value={d.targets[b]} inputMode="decimal"
                    onChange={(e) => setD({ ...d, targets: { ...d.targets, [b]: e.target.value } })} className="h-8 rounded-md border bg-background px-2 text-right text-sm tabular" />
                </li>);
            })}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            <button onClick={() => onPreview(d)} disabled={busy} className="rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent disabled:opacity-60">{busy ? "계산 중…" : "미리보기"}</button>
            <button onClick={() => onSave(d)} disabled={saving} className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60">저장</button>
            <button onClick={() => setD({ ...d, targets: Object.fromEntries(ORDER.map((b) => [b, String(Math.round(p.buckets.find((x) => x.key === b)?.current_pct ?? 0))])) as Record<Bucket, string> })}
              className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">현재 비중으로 채우기</button>
            {previewing && <button onClick={onReset} className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-accent">저장된 값으로</button>}
          </div>
        </div>
      </div>
    </Section>
  );
}

function Bar({ value, className }: { value: number; className?: string }) {
  return <div className="h-2 rounded-sm bg-muted"><div className={cn("h-full rounded-sm", className)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>;
}

function DividendCard({ p }: { p: Plan }) {
  const d = p.dividend;
  const have = d.basis === "net" ? d.net_krw : d.gross_krw;
  const after = d.basis === "net" ? d.after_net_krw : d.after_gross_krw;
  const changed = Math.abs(after - have) >= 1;
  return (
    <Section title="연간 배당" subtitle={`최근 12개월 실제 배당 × 보유 수량 · ${d.basis === "net" ? "세후 (미국 15% · 국내 15.4% 원천징수 후)" : "세전"}`}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
        <div><div className="text-[11px] text-muted-foreground">예상 연간 배당 ({d.basis === "net" ? "세후" : "세전"})</div>
          <div className="text-2xl font-semibold tabular">{won(have)}</div><div className="text-xs text-muted-foreground">월 {won(have / 12)} · 세전 {won(d.gross_krw)}</div></div>
        {isNum(d.target_krw) && <div><div className="text-[11px] text-muted-foreground">목표</div><div className="text-lg font-semibold tabular">{won(d.target_krw)}</div>
          <div className={cn("text-xs tabular", (d.gap_krw ?? 0) > 0 ? "text-warn" : "text-up")}>{(d.gap_krw ?? 0) > 0 ? `${won(d.gap_krw)} 부족` : "목표 달성"}</div></div>}
        {changed && <div><div className="text-[11px] text-muted-foreground">추천 매매 후</div><div className="text-lg font-semibold tabular">{won(after)}</div>
          <div className={cn("text-xs tabular", signClass(after - have))}>{won(after - have, true)}</div></div>}
      </div>
      {isNum(d.progress_pct) && (
        <div className="mt-3 space-y-1">
          <Bar value={d.progress_pct} className="bg-up" />
          <div className="flex justify-between text-[11px] text-muted-foreground"><span>목표 달성률 {d.progress_pct.toFixed(0)}%</span>{changed && isNum(d.after_progress_pct) && <span>매매 후 {d.after_progress_pct.toFixed(0)}%</span>}</div>
        </div>
      )}
      {d.need && (
        <p className="mt-3 rounded-md border bg-muted/30 p-2 text-xs">
          부족분을 <b>{d.need.name} ({d.need.symbol}, {d.basis === "net" ? "세후 " : ""}{(d.need.yield * 100).toFixed(1)}%)</b>로만 채우면 약 <b>{won(d.need.amount_krw)}</b>어치가 필요합니다.
          {isNum(d.need.bucket_pct) ? <> 지금 자산 안에서 옮긴다면 배당 그룹 목표를 <b>{d.need.bucket_pct.toFixed(0)}%</b> 정도로 잡으면 됩니다.</> : " 지금 자산만으로는 부족해 추가 자금이 필요합니다."}
        </p>
      )}
      {d.top.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs">{d.top.map((t) => (
          <li key={t.symbol} className="flex justify-between gap-2"><span className="truncate"><span className="font-mono font-semibold">{t.symbol}</span> <span className="text-muted-foreground">{t.name}</span></span>
            <span className="shrink-0 tabular">{won(t.div_krw)} <span className="text-muted-foreground">({pct(t.yield * 100, 1, false)})</span></span></li>))}</ul>
      )}
      {d.irregular_krw > 0 && (
        <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">계산에서 뺀 비정기 분배금 {won(d.irregular_krw)} (레버리지 ETF 연말 분배 등)</summary>
          <ul className="mt-1 space-y-0.5">{d.irregular.map((x) => <li key={x.symbol}>· <span className="font-mono">{x.symbol}</span> {won(x.amount_krw)} - {x.reason}</li>)}</ul></details>
      )}
    </Section>
  );
}

function TaxCard({ p }: { p: Plan }) {
  const t = p.tax;
  const hasTrades = p.trades.length > 0;
  const rows: [string, string, number, string?][] = [
    ["해외주식 양도세 (올해 실현분)", `실현 손익 ${won(t.realized_overseas_krw, true)}`, t.overseas_now_krw],
    ...(hasTrades ? [["해외주식 양도세 (추천 매매까지 실행 시)", `실현 손익 ${won(t.realized_overseas_krw + t.planned_realized_krw, true)}`, t.overseas_with_plan_krw,
      t.planned_unknown.length ? `평단 미입력 ${t.planned_unknown.join(", ")} 제외` : undefined] as [string, string, number, string?]] : []),
    ["배당 원천징수 (연간 예상)", `미국 ${won(t.dividend_withheld_krw.USD ?? 0)} · 국내 ${won(t.dividend_withheld_krw.KRW ?? 0)}`, t.dividend_withheld_total_krw, "배당 받을 때 이미 떼고 들어옴"],
    ...(hasTrades && t.transaction_with_plan_krw > 0 ? [["국내 증권거래세 (추천 매도분)", "매도 금액의 0.20%", t.transaction_with_plan_krw] as [string, string, number]] : []),
  ];
  return (
    <Section title="올해 낼 세금 (추정)" subtitle="2026년 세법 · 한국 거주 개인 기준">
      <table className="w-full text-sm tabular"><tbody>
        {rows.map(([k, sub, v, note]) => (
          <tr key={k} className="border-b last:border-0"><td className="py-2 pr-2"><div>{k}</div><div className="text-[11px] text-muted-foreground">{sub}{note ? ` · ${note}` : ""}</div></td>
            <td className="py-2 text-right font-semibold">{won(v)}</td></tr>))}
        <tr><td className="pt-2 font-medium">합계{hasTrades ? " (추천 매매 포함)" : ""}</td><td className="pt-2 text-right text-lg font-semibold">{won(hasTrades ? t.total_with_plan_krw : t.total_now_krw)}</td></tr>
      </tbody></table>
      <div className="mt-3 space-y-1.5 text-xs">
        <p className="text-muted-foreground">지금 해외 종목을 전부 판다고 가정하면 올해 해외 손익은 <b className={signClass(t.if_all_sold_gain_krw)}>{won(t.if_all_sold_gain_krw, true)}</b> → 양도세 <b>{won(t.overseas_if_all_sold_krw)}</b> (평단 입력된 종목만).</p>
        {t.harvest_saving_krw > 0 && (
          <p className="rounded-md border border-up/30 bg-up/5 p-2">참고: 평가손실 중인 해외 종목 합계 {won(t.harvestable_loss_krw)}. 연내 매도해 손실을 확정하면 같은 해 이익과 상계돼 양도세가 최대 <b>{won(t.harvest_saving_krw)}</b> 줄어듭니다 (손실은 다음 해로 이월되지 않음).</p>
        )}
        {t.financial_income_warning && <p className="text-warn">배당이 연 2,000만원을 넘어 금융소득종합과세 대상이 될 수 있습니다.</p>}
        <details className="text-muted-foreground"><summary className="cursor-pointer"><Info className="mr-1 inline size-3" />계산 기준</summary>
          <ul className="mt-1 space-y-0.5">{t.notes.map((n) => <li key={n}>· {n}</li>)}</ul></details>
      </div>
    </Section>
  );
}

function AllocationCard({ p }: { p: Plan }) {
  const hasTarget = p.buckets.some((b) => isNum(b.target_pct));
  return (
    <Section title="자산 비중" subtitle={hasTarget ? "현재 · 목표 · 추천 매매 후" : "목표 비중을 넣으면 맞추는 매매를 제안합니다"}>
      <div className="flex h-3 overflow-hidden rounded-sm">{p.buckets.map((b) => <div key={b.key} className={COLOR[b.key]} style={{ width: `${b.current_pct}%` }} title={`${b.label} ${b.current_pct.toFixed(1)}%`} />)}</div>
      <table className="mt-3 w-full text-sm tabular">
        <thead><tr className="border-b text-left text-[11px] text-muted-foreground"><th className="py-1.5 font-normal">그룹</th><th className="font-normal">현재</th>
          {hasTarget && <><th className="font-normal">목표</th><th className="font-normal">매매 후</th></>}<th className="font-normal text-right">평가금액</th></tr></thead>
        <tbody>{p.buckets.map((b) => {
          const off = isNum(b.target_pct) ? b.current_pct - b.target_pct : 0;
          return (
            <tr key={b.key} className="border-b last:border-0">
              <td className="py-2"><span className="flex items-center gap-2"><span className={cn("size-2.5 rounded-sm", COLOR[b.key])} />{b.label}<span className="text-[11px] text-muted-foreground">{b.count}</span></span></td>
              <td>{b.current_pct.toFixed(1)}%{hasTarget && Math.abs(off) >= 1 && <span className={cn("ml-1 text-[11px]", off > 0 ? "text-down" : "text-warn")}>{off > 0 ? `+${off.toFixed(1)}` : off.toFixed(1)}%p</span>}</td>
              {hasTarget && <><td>{isNum(b.target_pct) ? `${b.target_pct.toFixed(1)}%` : "—"}</td><td className="font-medium">{b.after_pct.toFixed(1)}%</td></>}
              <td className="text-right">{won(b.current_krw)}</td>
            </tr>);
        })}</tbody>
      </table>
    </Section>
  );
}

function TradesCard({ p }: { p: Plan }) {
  const sells = p.trades.filter((t) => t.side === "sell"), buys = p.trades.filter((t) => t.side === "buy");
  if (!p.buckets.some((b) => isNum(b.target_pct))) return null;
  const d = p.dividend;
  const have = d.basis === "net" ? d.net_krw : d.gross_krw, after = d.basis === "net" ? d.after_net_krw : d.after_gross_krw;
  const qty = (t: Trade) => (t.currency === "KRW" ? `${t.shares.toLocaleString("ko-KR")}주` : `${t.shares.toLocaleString("ko-KR", { maximumFractionDigits: 4 })}주`);
  const Row = ({ t }: { t: Trade }) => (
    <li className="grid gap-1 border-b py-2 last:border-0 sm:grid-cols-[150px_1fr_auto] sm:items-center sm:gap-3">
      <span className="flex items-center gap-2">
        {t.side === "sell" ? <ArrowDownRight className="size-4 text-down" /> : <ArrowUpRight className="size-4 text-up" />}
        <Link href={`/stock/${t.symbol}`} className="font-mono font-semibold hover:underline">{t.symbol}</Link>
        {t.side === "buy" && !t.held && <Pill tone="primary">신규</Pill>}{t.all && <Pill tone="down">전량</Pill>}
      </span>
      <span className="text-xs"><span className="block truncate text-muted-foreground">{t.name}</span>{t.reason}</span>
      <span className="text-right text-sm tabular"><b className={t.side === "sell" ? "text-down" : "text-up"}>{t.side === "sell" ? "매도" : "매수"} {won(t.amount_krw)}</b>
        <span className="block text-[11px] text-muted-foreground">약 {qty(t)} × {t.currency === "USD" ? "$" : ""}{price(t.price, t.currency)}{t.currency === "KRW" ? "원" : ""}
          {t.side === "sell" && isNum(t.realized_krw) && <> · 실현 <span className={signClass(t.realized_krw)}>{won(t.realized_krw, true)}</span></>}
          {Math.abs(t.div_net_change_krw) >= 1 && <> · 배당 {won(t.div_net_change_krw, true)}/년</>}</span></span>
    </li>
  );
  return (
    <Section title="추천 매매" subtitle="과대 그룹은 판단 점수가 낮은 종목부터 팔고, 부족한 그룹은 점수가 좋은 종목(배당 목표가 남았으면 배당수익률 우선)을 삽니다">
      {p.trades.length > 0 && (
        <p className="mb-2 rounded-md border bg-muted/30 p-2 text-sm">
          매도 {sells.length}건 <b>{won(sells.reduce((s, t) => s + t.amount_krw, 0))}</b> → 매수 {buys.length}건 <b>{won(buys.reduce((s, t) => s + t.amount_krw, 0))}</b>
          {Math.abs(after - have) >= 1 && <> · 연 배당({d.basis === "net" ? "세후" : "세전"}) {won(have)} → <b>{won(after)}</b>{isNum(d.target_krw) && isNum(d.after_progress_pct) ? ` (목표의 ${d.after_progress_pct.toFixed(0)}%)` : ""}</>}
        </p>
      )}
      {sells.length > 0 && <><h3 className="text-xs font-medium text-muted-foreground">매도</h3><ul>{sells.map((t) => <Row key={`s${t.symbol}`} t={t} />)}</ul></>}
      {buys.length > 0 && <><h3 className="mt-3 text-xs font-medium text-muted-foreground">매수</h3><ul>{buys.map((t) => <Row key={`b${t.symbol}`} t={t} />)}</ul></>}
      {p.notes.length > 0 && <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">{p.notes.map((n) => <li key={n}>· {n}</li>)}</ul>}
      <p className="mt-2 text-[11px] text-muted-foreground">규칙 기반 제안이며 투자 권유가 아닙니다. 실제 주문은 직접 판단해 증권사 앱에서 하세요.</p>
    </Section>
  );
}

function HoldingsCard({ p, onOverride }: { p: Plan; onOverride: (symbol: string, bucket: Bucket | null) => void }) {
  return (
    <Section title="종목별 분류 · 배당" subtitle="분류가 틀렸으면 바꿀 수 있습니다 (저장됨)">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm tabular">
          <thead><tr className="border-b text-left text-[11px] text-muted-foreground">{["종목", "그룹", "평가금액", "배당수익률", "연 배당 (세전)", "배당 증가", "판단 점수"].map((h) => <th key={h} className="py-1.5 pr-3 font-normal">{h}</th>)}</tr></thead>
          <tbody>{p.holdings.map((h) => (
            <tr key={h.id} className="border-b last:border-0">
              <td className="py-1.5 pr-3"><Link href={`/stock/${h.symbol}`} className="font-mono font-semibold hover:underline">{h.symbol}</Link><span className="block max-w-48 truncate text-[11px] text-muted-foreground">{h.name}</span></td>
              <td className="pr-3">
                <select value={h.bucket} aria-label={`${h.symbol} 그룹`} title={h.overridden ? `직접 지정 (자동: ${LABEL[h.bucket_auto]})` : h.bucket_reason}
                  onChange={(e) => onOverride(h.symbol, e.target.value === h.bucket_auto ? null : (e.target.value as Bucket))}
                  className={cn("h-7 rounded border bg-background px-1 text-xs", h.overridden && "border-primary")}>
                  {ORDER.map((b) => <option key={b} value={b}>{LABEL[b]}</option>)}
                </select>
                <span className="block text-[10px] text-muted-foreground">{h.overridden ? "직접 지정" : h.bucket_reason}</span>
              </td>
              <td className="pr-3">{won(h.value_krw)}</td>
              <td className="pr-3">{isNum(h.yield) && h.yield > 0 ? pct(h.yield * 100, 2, false) : "—"}</td>
              <td className="pr-3">{h.div_krw > 0 ? won(h.div_krw) : h.div_irregular_krw > 0 ? <span className="text-[11px] text-muted-foreground" title="비정기 분배라 배당 목표 계산에서 제외">비정기 {won(h.div_irregular_krw)}</span> : "—"}</td>
              <td className="pr-3 text-xs">{h.raise_streak ? `${h.raise_streak}년 연속` : "—"}{isNum(h.growth_5y) && <span className="block text-[11px] text-muted-foreground">5년 연 {pct(h.growth_5y * 100, 1)}</span>}</td>
              <td className="pr-3 text-xs">{isNum(h.score) ? h.score.toFixed(1) : "—"} <span className="text-muted-foreground">{h.label ?? ""}</span></td>
            </tr>))}</tbody>
        </table>
      </div>
    </Section>
  );
}
