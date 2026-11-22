"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { ChevronDown, Clock3, ExternalLink } from "lucide-react";
import { Fragment, type ReactNode, useState } from "react";

import { Pill, QueryState, Section, SkeletonBlock, UpdatedAt } from "@/components/common";
import { api } from "@/lib/api";
import { ago, timeKST } from "@/lib/format";

interface Source { title: string; source: string | null; url: string | null; published_at: string | null }
interface Story {
  key: string; name: string; move: number; unit: string; direction: "up" | "down" | "flat"; weight: number; sentence: string;
  detail: string | null; moves: { name: string; text: string; move: number }[];
  causes: { cause: string; marker: string; sources: Source[] }[]; has_cause: boolean;
}
interface Recap {
  hours: number; since: string; until: string; paragraphs: [string, string];
  lead: Story | null; stories: Story[]; others: { name: string; text: string; move: number }[]; extras: string[];
  weights: { kind: string; label: string; weight: number; basis: string }[];
  themes: { category: string; label: string; weight: number; count: number; tone: string;
    top: { title: string; source: string; url: string; age_h: number };
    items: { title: string; source: string; score: number; url: string }[] }[];
  method: string; updated_at: string;
}

const tone = (v: number) => (v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground");

/** The sentence with every cause phrase highlighted, so the "why" stands out from the "what". */
function Highlighted({ text, causes }: { text: string; causes: string[] }) {
  const parts: ReactNode[] = [];
  let rest = text;
  let k = 0;
  while (rest) {
    const hits = causes.map((c) => ({ c, i: rest.indexOf(c) })).filter((h) => h.c && h.i >= 0).sort((a, b) => a.i - b.i);
    if (!hits.length) { parts.push(rest); break; }
    const h = hits[0];
    if (h.i > 0) parts.push(rest.slice(0, h.i));
    parts.push(<span key={k++} className="font-semibold text-foreground underline decoration-gold/60 decoration-2 underline-offset-[5px]">{h.c}</span>);
    rest = rest.slice(h.i + h.c.length);
  }
  return <>{parts.map((p, i) => <Fragment key={i}>{p}</Fragment>)}</>;
}

function MoveChips({ moves }: { moves: Story["moves"] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {moves.map((m) => (
        <span key={m.name} className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium tabular",
          m.move > 0 ? "border-up/25 bg-up/[0.07] text-up" : m.move < 0 ? "border-down/25 bg-down/[0.07] text-down" : "border-border text-muted-foreground")}>
          {m.text}
        </span>
      ))}
    </span>
  );
}

function Sources({ story }: { story: Story }) {
  const list = story.causes.flatMap((c) => c.sources.slice(0, 1));
  if (!list.length) return null;
  return (
    <span className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
      {list.map((s, i) => (
        <a key={i} href={s.url ?? undefined} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 hover:text-foreground">
          <ExternalLink className="size-3 shrink-0" aria-hidden />
          <span className="truncate">{s.source}{s.published_at ? ` · ${ago(s.published_at)}` : ""} — {s.title}</span>
        </a>
      ))}
    </span>
  );
}

/** Home: what moved in the last 12 hours and why, most important first. */
export function RecapCard() {
  const q = useQuery({ queryKey: ["recap", 12], queryFn: () => api<Recap>("/api/market/recap?hours=12"), staleTime: 300_000, refetchInterval: 600_000 });
  const [open, setOpen] = useState(false);
  return (
    <Section title={<span className="flex items-center gap-2"><Clock3 className="size-4 text-gold" />지난 12시간 요약</span>}
      subtitle={q.data ? `${timeKST(q.data.since)} ~ ${timeKST(q.data.until)} · 무엇이 왜 움직였는지 중요도 순으로` : undefined}
      right={<UpdatedAt at={q.data?.updated_at} />}>
      <QueryState q={q} label="12시간 요약" skeleton={<SkeletonBlock rows={6} />}>
        {(d) => {
          const max = Math.max(1, ...d.weights.map((w) => w.weight));
          const stories = d.stories ?? [];
          const rest = stories.filter((s) => s.key !== d.lead?.key);
          return (
            <div className="space-y-5">
              {d.lead ? (
                <div className="rounded-xl border border-gold/25 bg-gradient-to-br from-gold/[0.07] via-transparent to-transparent p-4">
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] font-medium tracking-[0.14em] text-gold">핵심 <MoveChips moves={d.lead.moves} /></div>
                  <p className="text-[17px] font-medium leading-8 text-foreground/90">
                    <Highlighted text={d.lead.sentence} causes={d.lead.causes.map((c) => c.cause)} />
                  </p>
                  <Sources story={d.lead} />
                </div>
              ) : <p className="text-[15px] leading-7">{d.paragraphs[0]}</p>}

              {rest.length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-medium text-muted-foreground">시장별 흐름과 원인</h3>
                  <ul className="divide-y divide-border/70">
                    {rest.map((s) => (
                      <li key={s.key} className="grid gap-x-4 gap-y-1 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                        <div>
                          <div className="text-[13px] font-semibold">{s.name}</div>
                          <div className="mt-1"><MoveChips moves={s.moves} /></div>
                        </div>
                        <div className="min-w-0">
                          <p className="text-[14.5px] leading-7 text-foreground/85">
                            <Highlighted text={s.sentence} causes={s.causes.map((c) => c.cause)} />
                          </p>
                          {s.has_cause ? <Sources story={s} /> : <span className="text-[11px] text-muted-foreground">같은 시간대 국내 기사에서 원인을 확인하지 못했습니다</span>}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {(d.others?.length > 0 || d.extras?.length > 0) && (
                <div className="space-y-1.5 rounded-xl bg-foreground/[0.025] px-4 py-3 text-[13px] leading-6 text-muted-foreground">
                  {d.others?.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5"><span className="mr-1 text-xs">그 밖에</span>
                      {d.others.map((o) => <span key={o.name} className={cn("tabular", tone(o.move))}>{o.text}</span>).reduce<ReactNode[]>((a, x, i) => (i ? [...a, <span key={`s${i}`} className="text-border">·</span>, x] : [x]), [])}
                    </div>
                  )}
                  {d.extras?.map((e) => <p key={e}>{e}</p>)}
                </div>
              )}

              {d.themes.length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-medium text-muted-foreground">많이 다뤄진 이슈</h3>
                  <ul className="space-y-2">
                    {d.themes.slice(0, 3).map((t, i) => (
                      <li key={t.category} className="flex gap-3 text-[13.5px]">
                        <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-gold/30 text-[10px] font-semibold text-gold tabular">{i + 1}</span>
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <b>{t.label}</b>
                            <Pill tone={t.tone === "긍정적" ? "up" : t.tone === "부정적" ? "down" : "default"}>{t.tone}</Pill>
                            <span className="text-[11px] text-muted-foreground">기사 {t.count}건</span>
                          </div>
                          {t.top && (
                            <a href={t.top.url} target="_blank" rel="noopener noreferrer" className="mt-0.5 block truncate text-[12.5px] text-muted-foreground hover:text-foreground">
                              {t.top.title} <span className="text-[11px]">({t.top.source})</span>
                            </a>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" aria-expanded={open}>
                <ChevronDown className={open ? "size-3.5 rotate-180" : "size-3.5"} />작성 방법 · 중요도 가중치 ({d.weights.length}개 항목)
              </button>
              {open && (
                <div className="grid gap-4 md:grid-cols-2">
                  <ul className="space-y-1 text-xs">
                    {d.weights.map((w, i) => (
                      <li key={i} className="grid grid-cols-[40px_1fr_44px] items-center gap-2" title={w.basis}>
                        <span className="text-muted-foreground">{w.kind}</span>
                        <span className="relative h-5 overflow-hidden rounded bg-muted">
                          <span className="absolute inset-y-0 left-0 bg-gold/25" style={{ width: `${(w.weight / max) * 100}%` }} />
                          <span className="relative block truncate px-1.5 leading-5">{w.label}</span>
                        </span>
                        <span className="text-right tabular">{w.weight.toFixed(1)}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="space-y-2 text-xs">
                    {d.themes.slice(0, 4).map((t) => (
                      <div key={t.category}>
                        <div className="font-medium">{t.label} <span className="text-muted-foreground">· {t.count}건 · {t.tone} · 중요도 {t.weight.toFixed(0)}</span></div>
                        <ul className="mt-0.5 space-y-0.5 text-muted-foreground">
                          {t.items.slice(0, 3).map((it) => <li key={it.url ?? it.title} className="truncate">· <a href={it.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{it.title}</a> ({it.source})</li>)}
                        </ul>
                      </div>
                    ))}
                    <p className="text-[11px] text-muted-foreground">{d.method}</p>
                  </div>
                </div>
              )}
            </div>
          );
        }}
      </QueryState>
    </Section>
  );
}
