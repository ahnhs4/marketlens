"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowDownUp, Grid3x3, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { PageHeader, QueryState, Section, SignalBadge, SkeletonBlock, scoreTone } from "@/components/common";
import { api, del, post } from "@/lib/api";
import { compact, frac, isNum, num, pct, price } from "@/lib/format";
import { structureKo } from "@/lib/i18n";

interface WL { id: number; name: string; symbols: string[] }
interface Row { symbol: string; name: string | null; price: number | null; change_pct: number | null; currency: string | null; market_cap: number | null; volume: number | null;
  delay_minutes: number | null; technical?: number; macro?: number | null; signal?: string; rsi?: number | null; trend?: string; mom20?: number | null; status?: string }

const SORTS: { key: keyof Row; label: string }[] = [
  { key: "technical", label: "점수" }, { key: "mom20", label: "모멘텀" }, { key: "change_pct", label: "당일 등락률" },
  { key: "rsi", label: "RSI" }, { key: "market_cap", label: "시가총액" }, { key: "volume", label: "거래량" },
];

export default function WatchlistPage() {
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["watchlists"], queryFn: () => api<WL[]>("/api/watchlists") });
  const [active, setActive] = useState<number | null>(null);
  const wid = active ?? lists.data?.[0]?.id ?? null;
  const rows = useQuery({ queryKey: ["wl-rows", wid], enabled: wid !== null, queryFn: () => api<{ rows: Row[]; name: string }>(`/api/watchlists/${wid}/quotes`), refetchInterval: 30_000 });
  const [sortKey, setSortKey] = useState<keyof Row>("technical");
  const [desc, setDesc] = useState(true);
  const [newName, setNewName] = useState("");
  const [sym, setSym] = useState("");
  const inval = () => { qc.invalidateQueries({ queryKey: ["watchlists"] }); qc.invalidateQueries({ queryKey: ["wl-rows"] }); };
  const create = useMutation({ mutationFn: () => post<WL>("/api/watchlists", { name: newName }), onSuccess: (w) => { setNewName(""); setActive(w.id); inval(); }, onError: (e: Error) => toast.error(e.message) });
  const add = useMutation({ mutationFn: () => post(`/api/watchlists/${wid}/items`, { symbol: sym }), onSuccess: () => { setSym(""); inval(); }, onError: (e: Error) => toast.error(e.message) });
  const remove = useMutation({ mutationFn: (s: string) => del(`/api/watchlists/${wid}/items/${encodeURIComponent(s)}`), onSuccess: inval });
  const drop = useMutation({ mutationFn: () => del(`/api/watchlists/${wid}`), onSuccess: () => { setActive(null); inval(); } });

  const sorted = useMemo(() => {
    const xs = [...(rows.data?.rows ?? [])];
    return xs.sort((a, b) => {
      const x = a[sortKey] as number | null | undefined, y = b[sortKey] as number | null | undefined;
      if (!isNum(x)) return 1;
      if (!isNum(y)) return -1;
      return desc ? y - x : x - y;
    });
  }, [rows.data, sortKey, desc]);

  return (
    <div className="space-y-5">
      <PageHeader title="관심종목" description="시세와 기술적 · 매크로 점수. 점수는 최신 스캔 결과를 쓰고, 없으면 즉시 계산합니다."
        right={<Link href="/matrix" className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:border-gold/40 hover:text-foreground"><Grid3x3 className="size-3.5" />신호 매트릭스</Link>} />
      <QueryState q={lists} label="관심종목">
        {(ls) => (
          <div className="flex flex-wrap items-center gap-2">
            {ls.map((w) => (
              <button key={w.id} onClick={() => setActive(w.id)} className={cn("rounded-md border px-3 py-1.5 text-sm", w.id === wid ? "bg-accent font-medium" : "text-muted-foreground hover:text-foreground")}>
                {w.name} <span className="text-xs text-muted-foreground">{w.symbols.length}</span>
              </button>
            ))}
            <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (newName.trim()) create.mutate(); }}>
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="새 목록 (예: 배당주)" className="h-8 w-44 rounded-md border bg-background px-2 text-sm" />
              <button className="rounded-md border p-1.5 hover:bg-accent" aria-label="목록 만들기"><Plus className="size-4" /></button>
            </form>
          </div>
        )}
      </QueryState>
      {wid !== null && (
        <Section title={rows.data?.name ?? "관심종목"}
          right={<>
            <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (sym.trim()) add.mutate(); }}>
              <input value={sym} onChange={(e) => setSym(e.target.value)} placeholder="종목 추가: AAPL, 삼성전자, 000660" className="h-8 w-56 rounded-md border bg-background px-2 text-sm" />
              <button className="rounded-md border p-1.5 hover:bg-accent" aria-label="종목 추가" disabled={add.isPending}><Plus className="size-4" /></button>
            </form>
            <button onClick={() => confirm("이 관심종목 목록을 삭제할까요?") && drop.mutate()} className="rounded-md border p-1.5 text-muted-foreground hover:bg-accent" aria-label="목록 삭제"><Trash2 className="size-4" /></button>
          </>}>
          <div className="mb-2 flex flex-wrap items-center gap-1 text-xs">
            <span className="text-muted-foreground">정렬:</span>
            {SORTS.map((s) => (
              <button key={s.key} onClick={() => { if (sortKey === s.key) setDesc(!desc); else { setSortKey(s.key); setDesc(true); } }}
                className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5", sortKey === s.key ? "bg-accent text-foreground" : "text-muted-foreground")}>
                {s.label}{sortKey === s.key && <ArrowDownUp className="size-3" />}
              </button>
            ))}
          </div>
          <QueryState q={rows} label="관심종목 시세" skeleton={<SkeletonBlock rows={6} />}>
            {() => sorted.length === 0 ? <p className="text-sm text-muted-foreground">목록이 비어 있습니다 - 위에서 종목을 추가하세요.</p> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-sm">
                  <thead><tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    {["종목", "현재가", "당일", "기술", "매크로", "신호", "RSI", "20일 수익률", "이평 배열", "시가총액 (USD)", ""].map((h) => <th key={h} className="py-2 pr-3 font-normal">{h}</th>)}
                  </tr></thead>
                  <tbody>{sorted.map((r) => (
                    <tr key={r.symbol} className="border-b last:border-0 hover:bg-accent/40">
                      <td className="py-2 pr-3"><Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="hover:underline"><span className="font-mono font-semibold">{r.symbol}</span><span className="block max-w-44 truncate text-[11px] text-muted-foreground">{r.name}</span></Link></td>
                      <td className="pr-3 tabular">{price(r.price, r.currency)}{isNum(r.delay_minutes) && r.delay_minutes > 0 && <span className="ml-1 text-[10px] text-warn">{r.delay_minutes}분 지연</span>}</td>
                      <td className={cn("pr-3 tabular", isNum(r.change_pct) && r.change_pct >= 0 ? "text-up" : "text-down")}>{pct(r.change_pct)}</td>
                      <td className={cn("pr-3 font-semibold tabular", scoreTone(r.technical))}>{num(r.technical, 0)}</td>
                      <td className={cn("pr-3 tabular", scoreTone(r.macro))}>{num(r.macro, 0)}</td>
                      <td className="pr-3">{r.signal ? <SignalBadge signal={r.signal} size="sm" /> : <span className="text-xs text-muted-foreground">{r.status ?? "—"}</span>}</td>
                      <td className="pr-3 tabular">{num(r.rsi, 0)}</td>
                      <td className="pr-3 tabular">{frac(r.mom20)}</td>
                      <td className="pr-3 text-xs">{r.trend ? structureKo(r.trend) : "—"}</td>
                      <td className="pr-3 tabular">{compact(r.market_cap, "USD")}</td>
                      <td><button onClick={() => remove.mutate(r.symbol)} className="rounded p-1 text-muted-foreground hover:bg-accent" aria-label={`${r.symbol} 삭제`}><X className="size-4" /></button></td>
                    </tr>))}</tbody>
                </table>
              </div>
            )}
          </QueryState>
        </Section>
      )}
    </div>
  );
}
