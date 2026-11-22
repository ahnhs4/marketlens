"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Info } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock } from "@/components/common";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, post } from "@/lib/api";
import { compact, pct } from "@/lib/format";

interface Holder { cik: number; manager: string; kind: "hedge" | "bank"; rep: string; photo: string | null; weight_pct: number; rank_in_book: number;
  value_usd: number; shares: number; change: "new" | "added" | "reduced" | "same" | null; change_pct: number | null; first_seen: string; first_seen_ko: string; filed: string }
interface Item { rank: number; cusip: string; ticker: string | null; name: string; avg_weight_pct: number; avg_added_pct: number; holders: number;
  value_usd: number; new: number; added: number; reduced: number; typical_since: string | null; detail: Holder[] }
interface Manager { cik: number; name: string; kind: string; kind_ko: string; rep: string; photo: string | null; equity_value_usd: number; positions: number; filed: string }
interface Status { state: string; done: number; total: number; message: string | null; filings: number }
interface Top { ready: boolean; period?: string; period_ko?: string; n_managers?: number; items: Item[]; managers: Manager[]; status: Status;
  next_deadline?: string; note?: string; metric?: string }

const quarterKo = (iso: string) => { const d = new Date(iso); return `${d.getFullYear()}년 ${Math.floor(d.getMonth() / 3) + 1}분기`; };

function Avatar({ name, photo, size = 32 }: { name: string; photo: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  const initials = name.split(/[\s.·]+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  if (photo && !broken) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={photo} alt="" width={size} height={size} onError={() => setBroken(true)} referrerPolicy="no-referrer"
      className="shrink-0 rounded-full border object-cover object-top" style={{ width: size, height: size }} />;
  }
  return <span aria-hidden className="grid shrink-0 place-items-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground" style={{ width: size, height: size }}>{initials}</span>;
}

function Change({ h }: { h: Holder }) {
  if (h.change === "new") return <Pill tone="up">신규 편입</Pill>;
  if (h.change === "added") return <Pill tone="up">추가 매수 {pct(h.change_pct, 0)}</Pill>;
  if (h.change === "reduced") return <Pill tone="down">축소 {pct(h.change_pct, 0)}</Pill>;
  if (h.change === "same") return <Pill>변동 없음</Pill>;
  return null;
}

export default function InstitutionsPage() {
  const qc = useQueryClient();
  const [group, setGroup] = useState<"all" | "hedge" | "bank">("all");
  const [metric, setMetric] = useState<"weight" | "bought">("weight");
  const [open, setOpen] = useState<Item | null>(null);
  const q = useQuery({
    queryKey: ["institutions", group, metric], staleTime: 600_000,
    queryFn: () => api<Top>(`/api/institutions/top?group=${group}&metric=${metric}`),
    refetchInterval: (query) => (query.state.data?.status?.state === "running" ? 15_000 : false),
  });
  const refresh = useMutation({ mutationFn: () => post<Status>("/api/institutions/refresh"), onSuccess: () => qc.invalidateQueries({ queryKey: ["institutions"] }) });
  const groupLabel = { all: "헤지펀드 + 은행", hedge: "헤지펀드·투자회사", bank: "은행" }[group];

  return (
    <div className="space-y-5">
      <PageHeader title="기관 포트폴리오 (13F)" description="자산 규모가 큰 헤지펀드 · 은행이 미국 SEC에 분기마다 공시하는 보유 내역(13F)으로, 평균적으로 포트폴리오의 몇 %를 어떤 주식에 담았는지 보여 줍니다." />
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Segmented size="xs" ariaLabel="기관 그룹" value={group} onChange={setGroup}
          options={[{ value: "all", label: "전체 Top 20" }, { value: "hedge", label: "헤지펀드" }, { value: "bank", label: "은행" }]} />
        <Segmented size="xs" ariaLabel="순위 기준" value={metric} onChange={setMetric}
          options={[{ value: "weight", label: "평균 보유 비중" }, { value: "bought", label: "최근 분기 순매수" }]} />
      </div>
      <QueryState q={q} label="13F" skeleton={<SkeletonBlock rows={10} />}>
        {(d) => {
          const st = d.status;
          const loading = st?.state === "running";
          return (
            <>
              {loading && (
                <div className="rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
                  SEC EDGAR에서 13F를 내려받는 중 ({st.done}/{st.total}) - {st.message}. 처음 한 번만 몇 분 걸리고, 이후엔 새 분기 공시만 받습니다.
                  {d.ready && " 아래 결과는 지금까지 받은 기관 기준입니다."}
                </div>
              )}
              {!d.ready && !loading && (
                <div className="space-y-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                  <p>아직 받은 13F 데이터가 없습니다 (Data unavailable).</p>
                  <button onClick={() => refresh.mutate()} className="rounded-md border px-3 py-1.5 text-sm font-medium text-foreground hover:bg-accent">지금 받기</button>
                </div>
              )}
              {d.ready && (
                <Section title={`Top ${d.items.length} 주식 · ${d.period_ko} 말 기준`}
                  subtitle={`${groupLabel} 중 13F 주식 보유액 상위 ${d.n_managers}곳 · ${metric === "weight" ? "각 기관 포트폴리오에서 차지하는 비중의 평균 (미보유 = 0%)" : "지난 분기에 산 금액이 포트폴리오에서 차지하는 비중의 평균 (판 금액은 차감)"}`}
                  right={d.next_deadline && <span>다음 공시 마감 {d.next_deadline.slice(5).replace("-", "/")}</span>}>
                  <ol className="divide-y">
                    {d.items.map((it) => (
                      <li key={it.cusip}>
                        <button onClick={() => setOpen(it)} className="grid w-full grid-cols-[28px_1fr_auto_16px] items-center gap-3 py-2.5 text-left hover:bg-accent/40">
                          <span className="text-center text-lg font-semibold tabular text-muted-foreground">{it.rank}</span>
                          <span className="min-w-0">
                            <span className="flex items-center gap-2"><span className="font-mono font-semibold">{it.ticker ?? "—"}</span><span className="truncate text-sm">{it.name}</span></span>
                            <span className="block text-[11px] text-muted-foreground">
                              {it.holders}/{d.n_managers}곳 보유{it.new ? ` · 신규 ${it.new}` : ""}{it.added ? ` · 추가 ${it.added}` : ""}{it.reduced ? ` · 축소 ${it.reduced}` : ""}
                              {it.typical_since ? ` · 보통 ${quarterKo(it.typical_since)}${it.typical_since === it.detail.map((x) => x.first_seen).sort()[0] ? " 이전" : ""}부터 보유` : ""}
                            </span>
                          </span>
                          <span className="text-right tabular">
                            <b className="text-base">{metric === "weight" ? `${it.avg_weight_pct.toFixed(2)}%` : pct(it.avg_added_pct, 2)}</b>
                            <span className="block text-[11px] text-muted-foreground">{metric === "weight" ? `순매수 ${pct(it.avg_added_pct, 2)}` : `평균 비중 ${it.avg_weight_pct.toFixed(2)}%`}</span>
                          </span>
                          <ChevronRight className="size-4 text-muted-foreground" />
                        </button>
                      </li>
                    ))}
                  </ol>
                  <p className="mt-2 flex gap-1.5 text-[11px] text-muted-foreground"><Info className="mt-0.5 size-3 shrink-0" />{d.note} 옵션(콜·풋)과 채권은 빼고 주식 보유분만 셉니다. 13F는 분기 말 기준이라 지금은 이미 사고팔았을 수 있습니다.</p>
                </Section>
              )}
              {d.ready && d.managers.length > 0 && (
                <Section title="포함된 기관" subtitle="13F 주식 보유액 순 · 사진은 대표 인물 (Wikipedia)">
                  <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    {d.managers.map((m) => (
                      <li key={m.cik} className="flex items-center gap-2 rounded-md border p-2">
                        <Avatar name={m.rep} photo={m.photo} />
                        <span className="min-w-0 text-xs"><span className="block truncate font-medium text-sm">{m.name}</span>
                          <span className="block truncate text-muted-foreground">{m.rep} · {m.kind_ko}</span>
                          <span className="block tabular text-muted-foreground">{compact(m.equity_value_usd, "USD")} · {m.positions.toLocaleString("ko-KR")}종목 · 공시 {m.filed.slice(5).replace("-", "/")}</span></span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </>
          );
        }}
      </QueryState>
      <Dialog open={!!open} onOpenChange={(o) => { if (!o) setOpen(null); }}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
          {open && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2 pr-8">
                  <span className="font-mono">{open.ticker ?? open.cusip}</span><span className="font-normal">{open.name}</span>
                </DialogTitle>
                <DialogDescription>
                  {open.holders}곳 보유 · 평균 비중 {open.avg_weight_pct.toFixed(2)}% · 지난 분기 순매수 {pct(open.avg_added_pct, 2)}
                  {open.ticker && <> · <Link href={`/stock/${open.ticker}`} className="underline">종목 분석 보기</Link></>}
                </DialogDescription>
              </DialogHeader>
              <ul className="divide-y">
                {open.detail.map((h) => (
                  <li key={h.cik} className="flex items-start gap-3 py-2">
                    <Avatar name={h.rep} photo={h.photo} size={36} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium">{h.manager}</span>
                        <span className="shrink-0 font-semibold tabular">{h.weight_pct.toFixed(2)}%</span>
                      </div>
                      <div className="text-[11px] text-muted-foreground">{h.rep} · 포트폴리오 {h.rank_in_book}위 · {compact(h.value_usd, "USD")}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                        <Change h={h} />
                        <span className="text-muted-foreground">{h.first_seen_ko}</span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-muted-foreground">사유: 공시에 없음</div>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-muted-foreground">언제 샀는지는 분기 단위로만 알 수 있습니다 (최근 4개 분기 공시 비교). 비중 = 그 기관의 13F 주식 보유액 중 이 종목의 비율.</p>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
