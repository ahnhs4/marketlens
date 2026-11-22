"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Lock, Pencil, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type ReactNode, Suspense, useState } from "react";
import { toast } from "sonner";

import { PageHeader, Pill, QueryState, Section, SkeletonBlock } from "@/components/common";
import { Switch } from "@/components/ui/switch";
import { api, del, post, put } from "@/lib/api";
import { currencySymbol, frac, isNum, num, price } from "@/lib/format";

// Trade journal: what I actually traded, which signal I followed, and how it went.
// Private - it lives in the local database only and is never part of the public demo.

interface Item {
  id: number; symbol: string; name: string | null; currency: string | null; strategy: string; signal: string | null; followed: boolean;
  entry_date: string; entry_price: number; quantity: number; stop_price: number | null; target_price: number | null;
  exit_date: string | null; exit_price: number | null; fees: number; note: string | null; closed: boolean;
  pnl: number | null; return: number | null; r_multiple: number | null; planned_rr: number | null; holding_days: number;
}
interface Stats {
  trades: number; closed: number; open: number; win_rate: number | null; avg_return: number | null; avg_win: number | null; avg_loss: number | null;
  profit_factor: number | null; expectancy_r: number | null; avg_holding_days: number | null; best: number | null; worst: number | null;
  pnl_by_currency: Record<string, number>; followed_rate: number | null;
}
interface Journal { items: Item[]; overall: Stats; by_strategy: (Stats & { strategy: string })[]; discipline: { followed: Stats; deviated: Stats }; note: string }
interface Draft {
  symbol: string; strategy: string; signal: string; followed: boolean; entry_date: string; entry_price: string; quantity: string;
  stop_price: string; target_price: string; exit_date: string; exit_price: string; fees: string; note: string;
}

const INPUT = "h-8 rounded-md border bg-background px-2 text-sm";
const today = () => new Date().toISOString().slice(0, 10);
const blank = (): Draft => ({ symbol: "", strategy: "", signal: "", followed: true, entry_date: today(), entry_price: "", quantity: "", stop_price: "", target_price: "", exit_date: "", exit_price: "", fees: "", note: "" });
const str = (v: number | null) => (v === null || v === undefined ? "" : String(v));
const toDraft = (t: Item): Draft => ({
  symbol: t.symbol, strategy: t.strategy === "미분류" ? "" : t.strategy, signal: t.signal ?? "", followed: t.followed, entry_date: t.entry_date, entry_price: str(t.entry_price), quantity: str(t.quantity),
  stop_price: str(t.stop_price), target_price: str(t.target_price), exit_date: t.exit_date ?? "", exit_price: str(t.exit_price), fees: t.fees ? String(t.fees) : "", note: t.note ?? "",
});
const opt = (v: string) => (v.trim() === "" ? null : Number(v));
const body = (d: Draft) => ({
  symbol: d.symbol.trim().toUpperCase(), strategy: d.strategy.trim(), signal: d.signal.trim() || null, followed: d.followed, entry_date: d.entry_date,
  entry_price: Number(d.entry_price), quantity: Number(d.quantity), stop_price: opt(d.stop_price), target_price: opt(d.target_price),
  exit_date: d.exit_date || null, exit_price: opt(d.exit_price), fees: opt(d.fees) ?? 0, note: d.note.trim() || null,
});
const money = (v: number | null, c?: string | null) => (isNum(v) ? `${v > 0 ? "+" : v < 0 ? "-" : ""}${currencySymbol(c)}${price(Math.abs(v), c)}` : "—");
const tone = (v: number | null | undefined) => (isNum(v) ? (v > 0 ? "text-up" : v < 0 ? "text-down" : "") : "");

function StatGrid({ s }: { s: Stats }) {
  const pnl = Object.entries(s.pnl_by_currency);
  const cells: [string, string, string][] = [
    ["기록한 거래", `${s.trades}건`, `종료 ${s.closed} · 보유 중 ${s.open}`],
    ["승률", frac(s.win_rate, 0, false), s.closed ? `종료 ${s.closed}건 기준` : "종료된 거래 없음"],
    ["평균 손익", frac(s.avg_return), `이익 ${frac(s.avg_win)} · 손실 ${frac(s.avg_loss)}`],
    ["손익비 (PF)", num(s.profit_factor, 2), "총이익 ÷ 총손실"],
    ["평균 R", isNum(s.expectancy_r) ? `${s.expectancy_r > 0 ? "+" : ""}${s.expectancy_r.toFixed(2)}R` : "—", "손절가를 적은 거래만"],
    ["평균 보유", isNum(s.avg_holding_days) ? `${s.avg_holding_days.toFixed(0)}일` : "—", `최고 ${frac(s.best)} · 최저 ${frac(s.worst)}`],
    ["신호 준수율", frac(s.followed_rate, 0, false), "규칙대로 한 거래의 비율"],
  ];
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 xl:grid-cols-7">
        {cells.map(([k, v, sub]) => (
          <div key={k}><dt className="text-[11px] text-muted-foreground">{k}</dt><dd className="text-lg font-semibold tabular">{v}</dd><dd className="text-[10.5px] text-muted-foreground tabular">{sub}</dd></div>
        ))}
      </dl>
      {pnl.length > 0 && <p className="mt-3 text-xs tabular text-muted-foreground">실현 손익 · {pnl.map(([c, v]) => <span key={c} className={cn("mr-3 font-medium", tone(v))}>{money(v, c)} <span className="font-normal text-muted-foreground">{c}</span></span>)}</p>}
    </>
  );
}

function StatsRow({ label, s }: { label: ReactNode; s: Stats }) {
  return (
    <tr className="border-t">
      <td className="py-1.5 pr-3 font-medium">{label}</td><td>{s.trades}</td><td>{s.closed}</td><td>{frac(s.win_rate, 0, false)}</td>
      <td className={tone(s.avg_return)}>{frac(s.avg_return)}</td><td className="text-up">{frac(s.avg_win)}</td><td className="text-down">{frac(s.avg_loss)}</td>
      <td>{num(s.profit_factor, 2)}</td><td className={tone(s.expectancy_r)}>{isNum(s.expectancy_r) ? `${s.expectancy_r.toFixed(2)}R` : "—"}</td>
      <td>{isNum(s.avg_holding_days) ? `${s.avg_holding_days.toFixed(0)}일` : "—"}</td>
    </tr>
  );
}
const STATS_HEAD = (first: string) => (
  <thead><tr className="text-left text-muted-foreground">{[first, "거래", "종료", "승률", "평균 손익", "평균 이익", "평균 손실", "손익비", "평균 R", "평균 보유"].map((h) => <th key={h} className="pr-3 font-normal whitespace-nowrap">{h}</th>)}</tr></thead>
);

function Editor({ initial, id, strategies, onClose }: { initial: Draft; id: number | null; strategies: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState(initial);
  const save = useMutation({
    mutationFn: () => (id === null ? post("/api/journal", body(d)) : put(`/api/journal/${id}`, body(d))),
    onSuccess: () => { toast.success(id === null ? "일지에 기록했습니다" : "수정했습니다"); qc.invalidateQueries({ queryKey: ["journal"] }); onClose(); },
    onError: (e: Error) => toast.error(e.message),
  });
  const f = (k: Exclude<keyof Draft, "followed">, label: string, extra: { type?: string; w?: string; required?: boolean; placeholder?: string; mono?: boolean; list?: string } = {}) => (
    <label className="text-xs">{label}
      <input type={extra.type ?? "text"} inputMode={extra.type ? undefined : extra.mono || extra.list || k === "signal" || k === "note" ? undefined : "decimal"} required={extra.required} list={extra.list}
        value={d[k]} onChange={(e) => setD({ ...d, [k]: e.target.value })} placeholder={extra.placeholder} className={cn(INPUT, "mt-1 block", extra.w ?? "w-28", extra.mono && "font-mono")} />
    </label>
  );
  return (
    <Section title={id === null ? "새 거래 기록" : `${initial.symbol} 수정`} subtitle="직접 넣은 주문을 적는 곳입니다 — 이 앱은 주문을 내지 않습니다"
      right={<button aria-label="닫기" onClick={onClose} className="rounded-md border p-1.5 text-muted-foreground hover:bg-accent/50"><X className="size-3.5" /></button>}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="flex flex-wrap items-end gap-3">
          {f("symbol", "종목", { required: true, placeholder: "NVDA", mono: true })}
          {f("strategy", "전략", { w: "w-48", placeholder: "내 규칙", list: "journal-strategies" })}
          <datalist id="journal-strategies">{strategies.map((s) => <option key={s} value={s} />)}</datalist>
          {f("signal", "따른 신호", { w: "w-72", placeholder: "예: RSI 과매도 반등 + 20·60 골든크로스" })}
          <label className="flex items-center gap-2 pb-1.5 text-xs"><Switch checked={d.followed} onCheckedChange={(v) => setD({ ...d, followed: v })} />규칙대로 했음</label>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          {f("entry_date", "매수일", { type: "date", w: "w-40", required: true })}
          {f("entry_price", "매수가", { required: true })}
          {f("quantity", "수량", { required: true, w: "w-24" })}
          {f("stop_price", "손절가 (선택)")}
          {f("target_price", "목표가 (선택)")}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          {f("exit_date", "매도일 (끝난 거래만)", { type: "date", w: "w-40" })}
          {f("exit_price", "매도가")}
          {f("fees", "수수료 · 세금 합계", { w: "w-32" })}
          {f("note", "메모", { w: "w-80", placeholder: "왜 샀는지, 무엇을 배웠는지" })}
          <button type="submit" disabled={save.isPending} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60">{id === null ? "기록" : "저장"}</button>
        </div>
      </form>
    </Section>
  );
}

function JournalInner() {
  const sp = useSearchParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["journal"], queryFn: () => api<Journal>("/api/journal") });
  const saved = useQuery({ queryKey: ["builder-strategies"], queryFn: () => api<{ items: { name: string }[] }>("/api/builder/strategies") });
  // Arriving from a stock's trade plan pre-fills the form.
  const fromPlan = sp.get("symbol");
  const [editing, setEditing] = useState<{ id: number | null; draft: Draft } | null>(() => fromPlan
    ? { id: null, draft: { ...blank(), symbol: fromPlan, entry_price: sp.get("entry") ?? "", stop_price: sp.get("stop") ?? "", target_price: sp.get("target") ?? "", quantity: sp.get("qty") ?? "" } } : null);
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/journal/${id}`), onSuccess: () => { toast("삭제했습니다"); qc.invalidateQueries({ queryKey: ["journal"] }); }, onError: (e: Error) => toast.error(e.message) });
  const names = [...new Set([...(saved.data?.items.map((s) => s.name) ?? []), ...(q.data?.by_strategy.map((s) => s.strategy).filter((s) => s !== "미분류") ?? []), "내 규칙", "추세 추종", "눌림목", "돌파"])];

  return (
    <div className="space-y-5">
      <PageHeader title="매매 일지" description="실제로 따른 신호와 결과를 적어 두면, 전략별 승률과 평균 손익이 자동으로 집계됩니다."
        right={<div className="flex items-center gap-2">
          <Pill><Lock className="mr-1 inline size-3" />비공개 · 내 컴퓨터에만 저장</Pill>
          <button onClick={() => setEditing({ id: null, draft: blank() })} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground"><Plus className="size-4" />거래 기록</button>
        </div>} />
      {editing && <Editor key={editing.id ?? "new"} initial={editing.draft} id={editing.id} strategies={names} onClose={() => setEditing(null)} />}
      <QueryState q={q} label="매매 일지" skeleton={<SkeletonBlock rows={8} />}>
        {(j) => j.items.length === 0 ? (
          <Section title="아직 기록이 없습니다">
            <p className="text-sm leading-relaxed text-muted-foreground">거래를 하나 기록하면 여기에 승률 · 평균 손익 · 손익비 · 평균 R이 전략별로 쌓입니다. 종목 페이지의 <b className="text-foreground">매매 계획</b> 카드에서 &lsquo;이 계획으로 매매 일지 쓰기&rsquo;를 누르면 매수가 · 손절가 · 목표가 · 수량이 채워진 채로 열립니다.</p>
          </Section>
        ) : (
          <>
            <Section title="전체 성과"><StatGrid s={j.overall} /></Section>
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
              <Section title="전략별 성과" subtitle="종료된 거래가 많은 순">
                <div className="overflow-x-auto"><table className="w-full text-xs tabular">{STATS_HEAD("전략")}
                  <tbody>{j.by_strategy.map((s) => <StatsRow key={s.strategy} label={s.strategy} s={s} />)}</tbody></table></div>
              </Section>
              <Section title="규칙을 지켰을 때 vs 어겼을 때" subtitle="신호대로 한 거래와 임의로 한 거래의 차이">
                <div className="overflow-x-auto"><table className="w-full text-xs tabular">{STATS_HEAD("구분")}
                  <tbody><StatsRow label={<span className="text-up">규칙대로</span>} s={j.discipline.followed} /><StatsRow label={<span className="text-warn">임의 매매</span>} s={j.discipline.deviated} /></tbody></table></div>
              </Section>
            </div>
            <Section title={`거래 기록 (${j.items.length}건)`} subtitle="최근 매수일 순">
              <div className="overflow-x-auto">
                <table className="w-full text-xs tabular">
                  <thead><tr className="text-left text-muted-foreground">{["종목", "전략 · 신호", "매수", "수량", "손절 / 목표", "매도", "손익", "수익률", "R", "보유", ""].map((h) => <th key={h} className="pr-3 font-normal whitespace-nowrap">{h}</th>)}</tr></thead>
                  <tbody>{j.items.map((t) => (
                    <tr key={t.id} className="border-t align-top">
                      <td className="py-2 pr-3"><Link href={`/stock/${encodeURIComponent(t.symbol)}`} className="font-medium hover:underline">{t.name ?? t.symbol}</Link><div className="font-mono text-[10.5px] text-muted-foreground">{t.symbol}</div></td>
                      <td className="max-w-64 py-2 pr-3"><div className="flex flex-wrap items-center gap-1.5"><span className="font-medium">{t.strategy}</span>{!t.followed && <Pill tone="warn">임의</Pill>}</div>
                        {t.signal && <div className="mt-0.5 text-muted-foreground">{t.signal}</div>}{t.note && <div className="mt-0.5 text-[10.5px] text-muted-foreground/80">메모 · {t.note}</div>}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">{price(t.entry_price, t.currency)}<div className="text-[10.5px] text-muted-foreground">{t.entry_date}</div></td>
                      <td className="py-2 pr-3">{t.quantity.toLocaleString()}</td>
                      <td className="py-2 pr-3 whitespace-nowrap"><span className="text-down">{price(t.stop_price, t.currency)}</span> / <span className="text-up">{price(t.target_price, t.currency)}</span>
                        {isNum(t.planned_rr) && <div className="text-[10.5px] text-muted-foreground">계획 손익비 {t.planned_rr.toFixed(1)} : 1</div>}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">{t.closed ? <>{price(t.exit_price, t.currency)}<div className="text-[10.5px] text-muted-foreground">{t.exit_date}</div></> : <Pill tone="primary">보유 중</Pill>}</td>
                      <td className={cn("py-2 pr-3 whitespace-nowrap", tone(t.pnl))}>{money(t.pnl, t.currency)}</td>
                      <td className={cn("py-2 pr-3 font-medium", tone(t.return))}>{frac(t.return)}</td>
                      <td className={cn("py-2 pr-3", tone(t.r_multiple))}>{isNum(t.r_multiple) ? `${t.r_multiple.toFixed(2)}R` : "—"}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">{t.holding_days}일</td>
                      <td className="py-2 text-right whitespace-nowrap">
                        <button aria-label={`${t.symbol} 수정`} onClick={() => { setEditing({ id: t.id, draft: toDraft(t) }); window.scrollTo({ top: 0, behavior: "smooth" }); }} className="rounded-md border p-1.5 text-muted-foreground hover:bg-accent/50 hover:text-foreground"><Pencil className="size-3.5" /></button>
                        <button aria-label={`${t.symbol} 삭제`} onClick={() => { if (window.confirm(`${t.symbol} ${t.entry_date} 기록을 삭제할까요?`)) remove.mutate(t.id); }} className="ml-1 rounded-md border p-1.5 text-muted-foreground hover:bg-accent/50 hover:text-down"><Trash2 className="size-3.5" /></button>
                      </td>
                    </tr>))}</tbody>
                </table>
              </div>
              <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">{j.note}</p>
            </Section>
          </>
        )}
      </QueryState>
    </div>
  );
}

export default function JournalPage() {
  return <Suspense fallback={<SkeletonBlock rows={6} />}><JournalInner /></Suspense>;
}
