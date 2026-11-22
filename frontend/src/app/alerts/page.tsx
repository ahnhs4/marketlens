"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { BellRing, CheckCheck, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { PageHeader, Pill, QueryState, Section, SkeletonBlock } from "@/components/common";
import { Switch } from "@/components/ui/switch";
import { api, del, patch, post } from "@/lib/api";
import { ago, timeKST } from "@/lib/format";

const PARAM_KO: Record<string, string> = { value: "기준값", period: "이평 기간(일)", direction: "방향", multiple: "배수", hours: "최근 시간" };
const DIR_KO: Record<string, string> = { any: "모두", up: "상향 돌파", down: "하향 이탈" };
const CH_KO: Record<string, string> = { web: "웹", email: "이메일", discord: "디스코드", telegram: "텔레그램" };

interface Cond { label: string; params: Record<string, number | string | null> }
interface Alert { id: number; symbol: string; condition_type: string; params: Record<string, number | string>; channels: string[]; is_active: boolean; cooldown_minutes: number; last_triggered_at: string | null; last_checked_at: string | null }
interface Ev { id: number; symbol: string; condition_type: string; message: string; triggered_at: string; read: boolean; delivered: Record<string, string> }

function AlertsInner() {
  const sp = useSearchParams();
  const qc = useQueryClient();
  const conds = useQuery({ queryKey: ["alert-conds"], queryFn: () => api<{ conditions: Record<string, Cond>; channels: Record<string, boolean> }>("/api/alerts/conditions"), staleTime: Infinity });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api<Alert[]>("/api/alerts") });
  const events = useQuery({ queryKey: ["alert-events"], queryFn: () => api<Ev[]>("/api/alerts/events?limit=100"), refetchInterval: 60_000 });
  const [form, setForm] = useState<{ symbol: string; type: string; params: Record<string, string>; channels: string[] }>({ symbol: sp.get("symbol") ?? "", type: "rsi_above", params: { value: "70" }, channels: ["web"] });
  const inval = () => { qc.invalidateQueries({ queryKey: ["alerts"] }); qc.invalidateQueries({ queryKey: ["alert-events"] }); qc.invalidateQueries({ queryKey: ["alert-events-unread"] }); };
  const create = useMutation({
    mutationFn: () => post("/api/alerts", { symbol: form.symbol, condition_type: form.type, channels: form.channels,
      params: Object.fromEntries(Object.entries(form.params).filter(([, v]) => v !== "").map(([k, v]) => [k, Number.isNaN(Number(v)) ? v : Number(v)])) }),
    onSuccess: () => { toast.success("알림을 만들었습니다"); inval(); }, onError: (e: Error) => toast.error(e.message),
  });
  const check = useMutation({ mutationFn: () => post<{ checked: number; fired: number; errors: number }>("/api/alerts/check"), onSuccess: (r) => { toast(`알림 ${r.checked}개 확인 · ${r.fired}개 발동`); inval(); } });
  const toggle = useMutation({ mutationFn: (a: Alert) => patch(`/api/alerts/${a.id}`, { is_active: !a.is_active }), onSuccess: inval });
  const remove = useMutation({ mutationFn: (id: number) => del(`/api/alerts/${id}`), onSuccess: inval });
  const markRead = useMutation({ mutationFn: () => post("/api/alerts/events/read"), onSuccess: inval });
  const askPerm = async () => {
    if (typeof Notification === "undefined") return toast.error("이 브라우저는 알림을 지원하지 않습니다");
    const r = await Notification.requestPermission();
    toast(r === "granted" ? "브라우저 알림이 켜졌습니다" : "알림이 허용되지 않았습니다");
  };
  const setType = (t: string) => {
    const defaults = conds.data?.conditions[t]?.params ?? {};
    setForm((f) => ({ ...f, type: t, params: Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, v === null ? "" : String(v)])) }));
  };
  const input = "h-8 rounded-md border bg-background px-2 text-sm";
  return (
    <div className="space-y-5">
      <PageHeader title="알림" description="조건은 일봉(및 저장된 뉴스) 기준으로 5분마다 확인합니다. 같은 알림이 반복되지 않도록 재알림 대기 시간이 있습니다."
        right={<div className="flex gap-2">
          <button onClick={askPerm} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"><BellRing className="size-3.5" />브라우저 알림 켜기</button>
          <button onClick={() => check.mutate()} disabled={check.isPending} className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"><RefreshCw className={cn("size-3.5", check.isPending && "animate-spin")} />지금 확인</button>
        </div>} />
      <Section title="새 알림">
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <label className="text-xs">종목<input required value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className={cn(input, "mt-1 block w-32 font-mono")} placeholder="NVDA" /></label>
          <label className="text-xs">조건
            <select value={form.type} onChange={(e) => setType(e.target.value)} className={cn(input, "mt-1 block")}>
              {conds.data && Object.entries(conds.data.conditions).map(([k, c]) => <option key={k} value={k}>{c.label}</option>)}
            </select>
          </label>
          {Object.keys(form.params).map((k) => (
            <label key={k} className="text-xs">{PARAM_KO[k] ?? k}
              {k === "direction" ? (
                <select value={form.params[k]} onChange={(e) => setForm({ ...form, params: { ...form.params, [k]: e.target.value } })} className={cn(input, "mt-1 block")}>
                  {["any", "up", "down"].map((d) => <option key={d} value={d}>{DIR_KO[d]}</option>)}
                </select>
              ) : <input value={form.params[k]} onChange={(e) => setForm({ ...form, params: { ...form.params, [k]: e.target.value } })} className={cn(input, "mt-1 block w-24 tabular")} required={k === "value"} />}
            </label>
          ))}
          <fieldset className="flex items-center gap-2 text-xs">
            {conds.data && Object.entries(conds.data.channels).map(([ch, ok]) => (
              <label key={ch} className={cn("inline-flex items-center gap-1", !ok && "text-muted-foreground")} title={ok ? "" : "서버에 설정되지 않음 (.env)"}>
                <input type="checkbox" disabled={!ok} checked={form.channels.includes(ch)} onChange={(e) => setForm({ ...form, channels: e.target.checked ? [...form.channels, ch] : form.channels.filter((c) => c !== ch) })} />{CH_KO[ch] ?? ch}
              </label>
            ))}
          </fieldset>
          <button className="inline-flex h-8 items-center gap-1 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground"><Plus className="size-4" />만들기</button>
        </form>
      </Section>
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="등록된 알림 규칙">
          <QueryState q={alerts} label="알림" skeleton={<SkeletonBlock rows={4} />}>
            {(as) => as.length === 0 ? <p className="text-sm text-muted-foreground">아직 알림이 없습니다.</p> : (
              <ul className="divide-y">{as.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div className="min-w-0">
                    <span className="font-mono font-semibold">{a.symbol}</span> <span>{conds.data?.conditions[a.condition_type]?.label ?? a.condition_type}</span>
                    <span className="ml-1 text-xs text-muted-foreground">{Object.entries(a.params).map(([k, v]) => `${PARAM_KO[k] ?? k}=${k === "direction" ? DIR_KO[String(v)] ?? v : v}`).join(", ")}</span>
                    <div className="text-[11px] text-muted-foreground">{a.channels.map((c) => CH_KO[c] ?? c).join(", ")}로 전송 · 마지막 확인 {a.last_checked_at ? ago(a.last_checked_at) : "아직 없음"}{a.last_triggered_at ? ` · 마지막 발동 ${ago(a.last_triggered_at)}` : ""}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={a.is_active} onCheckedChange={() => toggle.mutate(a)} aria-label="활성화" />
                    <button onClick={() => remove.mutate(a.id)} className="rounded p-1 text-muted-foreground hover:bg-accent" aria-label="알림 삭제"><Trash2 className="size-4" /></button>
                  </div>
                </li>))}</ul>
            )}
          </QueryState>
        </Section>
        <Section title="발동 기록" right={<button onClick={() => markRead.mutate()} className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><CheckCheck className="size-3.5" />모두 읽음 처리</button>}>
          <QueryState q={events} label="알림 기록" skeleton={<SkeletonBlock rows={4} />}>
            {(es) => es.length === 0 ? <p className="text-sm text-muted-foreground">아직 발동된 알림이 없습니다.</p> : (
              <ul className="divide-y">{es.map((e) => (
                <li key={e.id} className={cn("py-2 text-sm", !e.read && "font-medium")}>
                  <div className="flex justify-between gap-2"><span>{e.message}</span>{!e.read && <Pill tone="primary">새 알림</Pill>}</div>
                  <div className="text-[11px] text-muted-foreground">{timeKST(e.triggered_at)} · {Object.entries(e.delivered).map(([k, v]) => `${CH_KO[k] ?? k}: ${v}`).join(" · ")}</div>
                </li>))}</ul>
            )}
          </QueryState>
        </Section>
      </div>
    </div>
  );
}

export default function AlertsPage() {
  return <Suspense fallback={<SkeletonBlock rows={6} />}><AlertsInner /></Suspense>;
}
