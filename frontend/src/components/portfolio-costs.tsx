"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { patch, post } from "@/lib/api";
import { price } from "@/lib/format";

/** Average-cost cell: shows "미입력" for stand-in costs and edits in place (Enter = save, Esc = cancel). */
export function CostCell({ pid, id, value, currency, estimated }: { pid: number; id: number; value: number; currency: string; estimated: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const save = useMutation({
    mutationFn: (v: number) => patch(`/api/portfolios/${pid}/positions/${id}`, { avg_cost: v }),
    onSuccess: () => { setEditing(false); qc.invalidateQueries({ queryKey: ["portfolio"] }); qc.invalidateQueries({ queryKey: ["portfolio-actions"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const submit = () => {
    const v = Number(text.replace(/[,$₩원\s]/g, ""));
    if (!Number.isFinite(v) || v <= 0) return toast.error("평단은 0보다 큰 숫자로 입력해 주세요");
    save.mutate(v);
  };
  if (editing) {
    return (
      <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)} inputMode="decimal" aria-label="평균 단가"
          onKeyDown={(e) => { if (e.key === "Enter") submit(); if (e.key === "Escape") setEditing(false); }}
          className="h-7 w-24 rounded border bg-background px-1.5 text-sm tabular" placeholder={currency === "KRW" ? "61200" : "29.85"} />
        <button onClick={submit} disabled={save.isPending} className="rounded p-0.5 hover:bg-accent" aria-label="평단 저장"><Check className="size-4 text-up" /></button>
        <button onClick={() => setEditing(false)} className="rounded p-0.5 hover:bg-accent" aria-label="취소"><X className="size-4" /></button>
      </span>
    );
  }
  return (
    <button onClick={(e) => { e.stopPropagation(); setText(estimated ? "" : String(value)); setEditing(true); }}
      className="group inline-flex items-center gap-1 rounded px-0.5 hover:bg-accent" aria-label="평균 단가 수정">
      {estimated ? <span className="rounded border border-warn/40 px-1 text-[11px] text-warn">미입력</span> : <span className="tabular">{price(value, currency)}</span>}
      <Pencil className="size-3 text-muted-foreground opacity-60 group-hover:opacity-100" />
    </button>
  );
}

interface BulkResult { updated: { symbol: string; avg_cost: number }[]; unmatched: { line: string; reason: string }[]; remaining_estimated: number }

/** Paste one holding per line ("NVDL 29.85", "005930 61,200") to set many average costs at once. */
export function BulkCostPanel({ pid, onDone }: { pid: number; onDone?: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [res, setRes] = useState<BulkResult | null>(null);
  const run = useMutation({
    mutationFn: () => post<BulkResult>(`/api/portfolios/${pid}/avg-costs`, { text }),
    onSuccess: (r) => {
      setRes(r);
      toast.success(`평단 ${r.updated.length}종목 반영${r.unmatched.length ? ` · ${r.unmatched.length}줄 확인 필요` : ""}`);
      qc.invalidateQueries({ queryKey: ["portfolio"] });
      qc.invalidateQueries({ queryKey: ["portfolio-actions"] });
      if (!r.unmatched.length) { setText(""); onDone?.(); }
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <p className="text-xs text-muted-foreground">한 줄에 한 종목씩 <b>종목 평단</b>을 붙여 넣으세요. 티커나 종목코드가 가장 정확합니다 (한글 종목명도 일부 인식).
        해외 종목은 <b>달러 평단 뒤에 원화 평단</b>까지 적으면 (예: <code>NVDL 30.03 43,202</code>) 매입 환율과 환차손익이 계산됩니다.</p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} aria-label="평단 일괄 입력"
        className="w-full rounded-md border bg-background p-2 font-mono text-sm" placeholder={"NVDL 30.03 43,202\nSOXL 120.4 171,500\n005930 61200\n미래에셋증권 18,500"} />
      <div className="flex items-center gap-2">
        <button onClick={() => run.mutate()} disabled={!text.trim() || run.isPending}
          className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-60">{run.isPending ? "적용 중…" : "평단 적용"}</button>
        {res && <span className="text-xs text-muted-foreground">반영 {res.updated.length} · 남은 평단 미입력 {res.remaining_estimated}종목</span>}
      </div>
      {res && res.unmatched.length > 0 && (
        <ul className="space-y-0.5 text-xs text-down">{res.unmatched.map((u, i) => <li key={i}>· {u.line} - {u.reason}</li>)}</ul>
      )}
    </div>
  );
}
