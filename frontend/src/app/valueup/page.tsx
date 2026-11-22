"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ArrowDownUp, ExternalLink, Search } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt, scoreTone } from "@/components/common";
import { type Evidence, EvidenceLine, won } from "@/components/stock/dart-panel";
import { api } from "@/lib/api";
import { isNum } from "@/lib/format";

type N = number | null;
interface Row {
  code: string; symbol: string; name: string; market: "KOSPI" | "KOSDAQ"; industry: string | null; cap: N; close: N; chg: N;
  pbr: N; roe: N; per: N; opm: N; rev_g: N; rev_state: string | null; op_g: N; op_state: string | null; debt: N; div_y: N;
  score: N; fin: boolean | null; basis: "parent" | "total" | null; period: string | null; impaired: boolean; cls: string | null;
  low_pbr: { pbr: number; gics_sector: string | null; published: string; pbr_date: string } | null;
  vu: { last_plan: string | null; plans: number; progress: number; pbr_plan: boolean; tags: string[]; url: string | null } | null;
}
interface Overview {
  available: boolean; dart_enabled: boolean; rows: Row[]; periods: string[]; errors: string[]; notes: string[]; source: string; updated_at: string;
  evidence: Evidence | null;
  dart_status: { state: string; processed: number; total: number; message: string | null };
  summary: {
    official: { n: number; kospi: number; kosdaq: number; median_pbr: N; published: string | null; sectors: { sector: string; n: number }[]; with_plan: number };
    below_book: Record<"KOSPI" | "KOSDAQ", { n: number; below_1: number; pct: N }>;
    valueup: { companies: number; pbr_plan: number; recent: { code: string; symbol: string; name: string; time: string; url: string | null; pbr: N; low_pbr: boolean }[] };
    classes: Record<string, number>;
  };
}

type Tab = "official" | "good" | "trap" | "valueup" | "all";
const TABS: { value: Tab; label: string }[] = [
  { value: "official", label: "거래소 저PBR 명단" }, { value: "good", label: "저평가 우량 후보" }, { value: "trap", label: "가치함정 주의" },
  { value: "valueup", label: "밸류업 공시 기업" }, { value: "all", label: "전체 상장사" },
];
const CLS_TONE: Record<string, "up" | "down" | "warn" | "default"> = { "저평가 우량 후보": "up", "가치함정 주의": "down", 자본잠식: "down", 저PBR: "warn",
  "이익 질 확인": "warn" };
const f1 = (v: N, d = 1, unit = "") => (isNum(v) ? `${v.toFixed(d)}${unit}` : "—");

function Kpi({ label, value, sub, children }: { label: string; value: React.ReactNode; sub?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-card">
      <div className="text-[11px] tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 font-display text-[40px] leading-none">{value}</div>
      {sub && <div className="mt-1.5 text-[12px] text-muted-foreground">{sub}</div>}
      {children}
    </div>
  );
}

/** PBR (log) × ROE map of every listed company; the official list in red, value-up planners in gold. */
function PbrRoeMap({ rows }: { rows: Row[] }) {
  const router = useRouter();
  const [hover, setHover] = useState<Row | null>(null);
  const pts = rows.filter((r) => isNum(r.pbr) && isNum(r.roe) && (r.pbr as number) > 0);
  const W = 820, H = 430, L = 44, B = 30, T = 22, R = 10;
  const lx = (p: number) => L + ((Math.log10(Math.min(Math.max(p, 0.05), 10)) - Math.log10(0.05)) / (Math.log10(10) - Math.log10(0.05))) * (W - L - R);
  const ly = (r: number) => T + (1 - (Math.min(Math.max(r, -30), 40) + 30) / 70) * (H - T - B);
  const layer = (r: Row) => (r.low_pbr ? 2 : r.vu?.last_plan ? 1 : 0);
  const sorted = [...pts].sort((a, b) => layer(a) - layer(b));
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="PBR과 ROE 분포">
        <rect x={L} y={ly(40)} width={lx(1) - L} height={ly(10) - ly(40)} className="fill-up/[0.06]" />
        <rect x={L} y={ly(3)} width={lx(1) - L} height={ly(-30) - ly(3)} className="fill-down/[0.06]" />
        {[0.1, 0.2, 0.5, 1, 2, 5, 10].map((p) => (
          <g key={p}>
            <line x1={lx(p)} x2={lx(p)} y1={T} y2={H - B} className={p === 1 ? "stroke-foreground/40" : "stroke-border"} strokeDasharray={p === 1 ? "4 3" : undefined} />
            <text x={lx(p)} y={H - B + 16} textAnchor="middle" className="fill-muted-foreground text-[11px]">{p}배</text>
          </g>
        ))}
        {[-30, -20, -10, 0, 3, 10, 20, 30, 40].map((r) => (
          <g key={r}>
            <line x1={L} x2={W - R} y1={ly(r)} y2={ly(r)} className={r === 10 || r === 3 ? "stroke-foreground/30" : r === 0 ? "stroke-foreground/20" : "stroke-border/60"} strokeDasharray={r === 10 || r === 3 ? "4 3" : undefined} />
            <text x={L - 6} y={ly(r) + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">{r}%</text>
          </g>
        ))}
        <text x={L + 8} y={ly(40) + 16} className="fill-up text-[12px] font-medium">저평가 우량 후보 · PBR&lt;1 · ROE≥10%</text>
        <text x={L + 8} y={ly(-30) - 8} className="fill-down text-[12px] font-medium">가치함정 주의 · PBR&lt;1 · ROE&lt;3%</text>
        <text x={W - R} y={H - 4} textAnchor="end" className="fill-muted-foreground text-[11px]">PBR (로그 축) →</text>
        <text x={L - 6} y={11} textAnchor="end" className="fill-muted-foreground text-[11px]">ROE ↑</text>
        {sorted.map((r) => (
          <circle key={r.code} cx={lx(r.pbr as number)} cy={ly(r.roe as number)} r={r.low_pbr ? 3.6 : r.vu?.last_plan ? 3 : 2.1}
            className={cn("cursor-pointer", r.low_pbr ? "fill-down" : r.vu?.last_plan ? "fill-gold" : "fill-muted-foreground/35")}
            opacity={r.low_pbr ? 0.9 : r.vu?.last_plan ? 0.8 : 1}
            onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)} onClick={() => router.push(`/stock/${encodeURIComponent(r.symbol)}`)} />
        ))}
      </svg>
      {hover && (
        <div className="pointer-events-none absolute right-2 top-2 rounded-lg border bg-popover px-3 py-2 text-xs shadow-card">
          <div className="font-medium">{hover.name} <span className="text-muted-foreground">{hover.code} · {hover.market === "KOSPI" ? "코스피" : "코스닥"}</span></div>
          <div className="tabular">PBR {f1(hover.pbr, 2, "배")} · ROE {f1(hover.roe, 1, "%")} · PER {f1(hover.per, 1, "배")} · 시총 {won(hover.cap)}원</div>
          {hover.low_pbr && <div className="text-down">거래소 저PBR 공표 (PBR {hover.low_pbr.pbr}배)</div>}
          {hover.vu?.last_plan && <div className="text-gold">기업가치 제고 계획 {hover.vu.last_plan.slice(0, 10)}</div>}
        </div>
      )}
      <div className="mt-1 flex flex-wrap gap-4 text-[11px] text-muted-foreground">
        <span><i className="mr-1 inline-block size-2 rounded-full bg-down align-middle" />거래소 저PBR 명단</span>
        <span><i className="mr-1 inline-block size-2 rounded-full bg-gold align-middle" />기업가치 제고 계획 공시</span>
        <span><i className="mr-1 inline-block size-2 rounded-full bg-muted-foreground/40 align-middle" />그 밖의 상장사 ({pts.length.toLocaleString()}곳 표시)</span>
        <span>점을 누르면 종목 화면으로 이동 · ROE −30~40%, PBR 0.05~10배 밖은 가장자리에 표시</span>
      </div>
    </div>
  );
}

type SortKey = "cap" | "pbr" | "roe" | "per" | "opm" | "rev_g" | "div_y" | "score" | "vu";
const val = (r: Row, k: SortKey): number | null => (k === "vu" ? (r.vu?.last_plan ? Date.parse(r.vu.last_plan) : null) : k === "pbr" ? (r.low_pbr?.pbr ?? r.pbr) : r[k]);

const HEADS: [SortKey, string][] = [["cap", "시가총액"], ["pbr", "PBR"], ["roe", "ROE"], ["per", "PER"], ["opm", "영업이익률"],
  ["rev_g", "매출 YoY"], ["div_y", "배당수익률"], ["score", "재무점수"], ["vu", "밸류업 공시"]];

function SortHead({ id, label, active, onSort }: { id: SortKey; label: string; active: boolean; onSort: (id: SortKey) => void }) {
  return (
    <th className="py-2 pr-3 text-right font-medium">
      <button onClick={() => onSort(id)} className={cn("inline-flex items-center gap-1 hover:text-foreground", active && "text-foreground")}>
        {label}<ArrowDownUp className="size-3 opacity-50" />
      </button>
    </th>
  );
}

function Table({ rows, tab }: { rows: Row[]; tab: Tab }) {
  const init: Record<Tab, [SortKey, boolean]> = { official: ["cap", true], good: ["score", true], trap: ["pbr", false], valueup: ["vu", true], all: ["cap", true] };
  const [sort, setSort] = useState<[SortKey, boolean] | null>(null);
  const [n, setN] = useState(50);
  const [k, desc] = sort ?? init[tab];
  const sorted = useMemo(() => [...rows].sort((a, b) => {
    const x = val(a, k), y = val(b, k);
    if (x === null) return 1;
    if (y === null) return -1;
    return desc ? y - x : x - y;
  }), [rows, k, desc]);
  const onSort = (id: SortKey) => setSort([id, id === k ? !desc : id !== "pbr" && id !== "per"]);
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1080px] text-[12.5px] tabular">
          <thead><tr className="border-b text-[11.5px] text-muted-foreground">
            <th className="py-2 pr-3 text-left font-medium">종목</th>
            <th className="py-2 pr-3 text-left font-medium">업종</th>
            {HEADS.map(([id, label]) => <SortHead key={id} id={id} label={label} active={id === k} onSort={onSort} />)}
            <th className="py-2 text-left font-medium">구분</th>
          </tr></thead>
          <tbody className="divide-y divide-border/60">
            {sorted.slice(0, n).map((r) => (
              <tr key={r.code} className="hover:bg-accent/50">
                <td className="py-2 pr-3">
                  <Link href={`/stock/${encodeURIComponent(r.symbol)}`} className="block hover:underline">
                    <span className="font-medium">{r.name}</span>
                    <span className="ml-1.5 text-[11px] text-muted-foreground">{r.code} · {r.market === "KOSPI" ? "코스피" : "코스닥"}</span>
                  </Link>
                </td>
                <td className="max-w-40 truncate py-2 pr-3 text-[11.5px] text-muted-foreground" title={r.industry ?? undefined}>{r.low_pbr?.gics_sector ?? r.industry ?? "—"}</td>
                <td className="py-2 pr-3 text-right">{won(r.cap)}</td>
                <td className="py-2 pr-3 text-right">
                  {r.low_pbr ? <span title={`거래소 산출 (${r.low_pbr.pbr_date} 기준)`}><b>{r.low_pbr.pbr.toFixed(2)}</b><span className="ml-1 text-[10.5px] text-muted-foreground">거래소</span></span>
                    : r.impaired ? <span className="text-down">자본잠식</span> : f1(r.pbr, 2)}
                </td>
                <td className={cn("py-2 pr-3 text-right", isNum(r.roe) && r.roe < 0 && "text-down")}>{f1(r.roe, 1, "%")}</td>
                <td className="py-2 pr-3 text-right">{f1(r.per, 1)}</td>
                <td className={cn("py-2 pr-3 text-right", isNum(r.opm) && r.opm < 0 && "text-down")}>{r.fin ? <span className="text-muted-foreground">금융</span> : f1(r.opm, 1, "%")}</td>
                <td className={cn("py-2 pr-3 text-right", isNum(r.rev_g) ? (r.rev_g >= 0 ? "text-up" : "text-down") : "text-muted-foreground")}>
                  {isNum(r.rev_g) ? `${r.rev_g >= 0 ? "+" : ""}${r.rev_g.toFixed(1)}%` : r.rev_state && r.rev_state !== "흑자" ? r.rev_state : "—"}
                </td>
                <td className="py-2 pr-3 text-right">{f1(r.div_y, 2, "%")}</td>
                <td className={cn("py-2 pr-3 text-right font-medium", scoreTone(r.score))}>{f1(r.score, 0)}</td>
                <td className="py-2 pr-3 text-right">
                  {r.vu?.last_plan ? (
                    <a href={r.vu.url ?? undefined} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
                      {r.vu.last_plan.slice(0, 10)}<ExternalLink className="size-3 opacity-60" />
                    </a>
                  ) : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="py-2">
                  <span className="flex flex-wrap gap-1">
                    {r.low_pbr && <Pill tone="down">저PBR 공표</Pill>}
                    {r.cls && r.cls !== "저PBR" && <Pill tone={CLS_TONE[r.cls]}>{r.cls}</Pill>}
                    {r.vu?.pbr_plan && <Pill>PBR 개선계획</Pill>}
                    {r.vu?.tags.includes("V100") && <Pill tone="primary">밸류업지수</Pill>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {sorted.length > n && (
        <button onClick={() => setN(n + 50)} className="mt-3 w-full rounded-lg border py-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">
          더 보기 ({n} / {sorted.length.toLocaleString()})
        </button>
      )}
      {!sorted.length && <p className="py-6 text-center text-sm text-muted-foreground">해당하는 종목이 없습니다</p>}
    </div>
  );
}

export default function ValueupPage() {
  const q = useQuery({ queryKey: ["valueup"], queryFn: () => api<Overview>("/api/kr/valueup"), staleTime: 600_000, refetchInterval: (qq) => (qq.state.data?.dart_status.state === "running" ? 30_000 : false) });
  const [tab, setTab] = useState<Tab>("official");
  const [mkt, setMkt] = useState<"ALL" | "KOSPI" | "KOSDAQ">("ALL");
  const [text, setText] = useState("");
  const rows = useMemo(() => {
    const all = q.data?.rows ?? [];
    const t = text.trim().replace(/\s/g, "").toLowerCase();
    return all.filter((r) => (mkt === "ALL" || r.market === mkt) && (!t || r.name.replace(/\s/g, "").toLowerCase().includes(t) || r.code.includes(t))
      && (tab === "official" ? !!r.low_pbr : tab === "good" ? r.cls === "저평가 우량 후보" : tab === "trap" ? r.cls === "가치함정 주의"
        : tab === "valueup" ? !!r.vu?.last_plan : true));
  }, [q.data, tab, mkt, text]);
  return (
    <div className="space-y-5">
      <PageHeader title="밸류업 · 저PBR" description="한국거래소가 공표한 저PBR 기업 명단과 기업가치 제고 계획 공시를, DART 재무제표로 계산한 전 상장사 PBR·ROE·재무 점수와 함께 봅니다." />
      <QueryState q={q} label="밸류업 현황" skeleton={<SkeletonBlock rows={12} />}>
        {(d) => {
          const s = d.summary;
          const maxSec = Math.max(1, ...s.official.sectors.map((x) => x.n));
          return (
            <>
              {!d.dart_enabled && (
                <div className="rounded-xl border border-warn/30 bg-warn/[0.06] px-4 py-3 text-[13px]">
                  DART 키가 아직 없어 거래소 명단·공시만 표시합니다. 프로젝트 <code>.env</code>에 <code>DART_API_KEY=발급받은키</code>를 넣고 백엔드를 재시작하면
                  전 상장사 PBR·ROE·재무 점수와 PBR-ROE 지도가 채워집니다.
                </div>
              )}
              {d.dart_status.state === "running" && (
                <div className="rounded-xl border border-gold/30 bg-gold/[0.06] px-4 py-3 text-[13px]">
                  DART 재무제표 수집 중 {d.dart_status.processed}/{d.dart_status.total} · {d.dart_status.message}
                </div>
              )}
              {d.errors.map((e) => <p key={e} className="text-xs text-down">{e} (Data unavailable)</p>)}

              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Kpi label="거래소 저PBR 공표 기업" value={<>{s.official.n}<span className="ml-1 text-base text-muted-foreground">곳</span></>}
                  sub={<>코스피 {s.official.kospi} · 코스닥 {s.official.kosdaq} · 중앙값 PBR {f1(s.official.median_pbr, 2, "배")}<br />{s.official.published} 공표 · 매년 5월·11월 첫 거래일 갱신</>} />
                <Kpi label="기업가치 제고 계획 공시 기업" value={<>{s.valueup.companies}<span className="ml-1 text-base text-muted-foreground">곳</span></>}
                  sub={<>PBR 개선계획 제출 {s.valueup.pbr_plan}곳 · 저PBR 명단 중 공시 이력 {s.official.with_plan}곳<br />2024년 5월 제도 시행 이후 누적</>} />
                <Kpi label="장부가보다 싸게 거래되는 상장사 (PBR < 1)"
                  value={isNum(s.below_book.KOSPI.pct) ? <>{s.below_book.KOSPI.pct.toFixed(0)}<span className="text-base text-muted-foreground">%</span><span className="mx-2 text-2xl text-muted-foreground">·</span>{f1(s.below_book.KOSDAQ.pct, 0)}<span className="text-base text-muted-foreground">%</span></> : "—"}
                  sub={isNum(s.below_book.KOSPI.pct) ? `코스피 ${s.below_book.KOSPI.below_1}/${s.below_book.KOSPI.n}곳 · 코스닥 ${s.below_book.KOSDAQ.below_1}/${s.below_book.KOSDAQ.n}곳 (DART 계산)` : "DART 키 설정 후 계산됩니다"} />
                <Kpi label="PBR 1배 미만 중 PBR-ROE 구분" value={isNum(s.classes["저평가 우량 후보"]) && d.dart_enabled ? <><span className="text-up">{s.classes["저평가 우량 후보"]}</span><span className="mx-2 text-2xl text-muted-foreground">·</span><span className="text-down">{s.classes["가치함정 주의"]}</span></> : "—"}
                  sub={d.dart_enabled ? <>저평가 우량 후보 (ROE≥10%) · 가치함정 주의 (ROE&lt;3%)<br />일회성 이익 의심 {s.classes["이익 질 확인"] ?? 0}곳 · 자본잠식 {s.classes["자본잠식"]}곳</> : "DART 키 설정 후 계산됩니다"} />
              </div>

              <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_340px]">
                <Section title="PBR × ROE 지도" subtitle="오른쪽일수록 비싸고, 위로 갈수록 자본을 잘 굴립니다 · 왼쪽 위 = 싸고 잘 버는 곳, 왼쪽 아래 = 싼 데는 이유가 있는 곳"
                  right={<UpdatedAt at={d.updated_at} />}>
                  {d.dart_enabled && d.rows.some((r) => isNum(r.pbr)) ? <PbrRoeMap rows={d.rows} />
                    : <p className="py-10 text-center text-sm text-muted-foreground">DART 재무제표가 수집되면 전 상장사 지도가 그려집니다 (Data unavailable)</p>}
                </Section>
                <div className="space-y-3">
                  <Section title="저PBR 명단 업종 분포" subtitle="GICS 업종 · 거래소 공표 기준">
                    <ul className="space-y-1.5 text-[12.5px]">
                      {s.official.sectors.map((x) => (
                        <li key={x.sector} className="grid grid-cols-[92px_1fr_28px] items-center gap-2">
                          <span className="truncate text-muted-foreground">{x.sector}</span>
                          <span className="h-2 rounded-full bg-muted"><span className="block h-2 rounded-full bg-down/70" style={{ width: `${(x.n / maxSec) * 100}%` }} /></span>
                          <span className="text-right tabular">{x.n}</span>
                        </li>
                      ))}
                    </ul>
                  </Section>
                  <Section title="최근 기업가치 제고 계획" subtitle="KIND 공시 원문 링크">
                    <ul className="space-y-1.5 text-[12.5px]">
                      {s.valueup.recent.slice(0, 8).map((v) => (
                        <li key={v.code + v.time} className="flex items-center justify-between gap-2">
                          <Link href={`/stock/${encodeURIComponent(v.symbol)}`} className="min-w-0 truncate hover:underline">
                            {v.name}{v.low_pbr && <span className="ml-1 text-[10.5px] text-down">저PBR</span>}
                          </Link>
                          <a href={v.url ?? undefined} target="_blank" rel="noopener noreferrer" className="inline-flex shrink-0 items-center gap-1 text-[11.5px] text-muted-foreground hover:text-foreground">
                            {v.time.slice(0, 10)}<ExternalLink className="size-3" />
                          </a>
                        </li>
                      ))}
                    </ul>
                  </Section>
                </div>
              </div>

              <Section title="종목 목록" subtitle={`${TABS.find((t) => t.value === tab)?.label} · ${rows.length.toLocaleString()}곳`}
                right={<span className="text-[11px]">재무 기준기간: {d.periods.length ? d.periods.slice(-2).join(", ") : "—"}</span>}>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <Segmented value={tab} onChange={setTab} options={TABS} ariaLabel="목록" />
                  <Segmented value={mkt} onChange={setMkt} options={[{ value: "ALL", label: "전체" }, { value: "KOSPI", label: "코스피" }, { value: "KOSDAQ", label: "코스닥" }]} ariaLabel="시장" />
                  <label className="ml-auto flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs">
                    <Search className="size-3.5 text-muted-foreground" />
                    <input value={text} onChange={(e) => setText(e.target.value)} placeholder="종목명 · 코드" className="w-36 bg-transparent outline-none" />
                  </label>
                </div>
                <Table key={tab} rows={rows} tab={tab} />
              </Section>

              <div className="space-y-1 text-[11.5px] leading-5 text-muted-foreground">
                <EvidenceLine ev={d.evidence} className="text-[11.5px] leading-5" />
                {d.notes.map((n) => <p key={n}>· {n}</p>)}
                <p>· 출처: {d.source}</p>
              </div>
            </>
          );
        }}
      </QueryState>
    </div>
  );
}
