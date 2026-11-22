"use client";

import { cn } from "cn";
import { useMemo, useState } from "react";

import { PageHeader, Pill, QueryState, Section, Segmented, SkeletonBlock, UpdatedAt } from "@/components/common";
import { ImpactChain, NewsRow, SummaryBlock } from "@/components/news";
import { useMarketNews } from "@/lib/hooks";
import { categoryKo } from "@/lib/i18n";
import type { NewsItem } from "@/lib/types";

const IMPACTS = ["All", "Medium+", "High+"] as const;
const SENTS = ["All", "Bullish", "Bearish", "Neutral"] as const;
const IMPACT_LABEL: Record<string, string> = { All: "영향도: 전체", "Medium+": "영향도: 보통 이상", "High+": "영향도: 높음 이상" };
const SENT_LABEL: Record<string, string> = { All: "전체", Bullish: "긍정", Bearish: "부정", Neutral: "중립" };

export default function NewsPage() {
  const q = useMarketNews();
  const [cat, setCat] = useState("All");
  const [imp, setImp] = useState<(typeof IMPACTS)[number]>("All");
  const [sent, setSent] = useState<(typeof SENTS)[number]>("All");
  const items = useMemo(() => {
    let xs: NewsItem[] = q.data?.items ?? [];
    if (cat !== "All") xs = xs.filter((x) => x.category === cat);
    if (imp === "Medium+") xs = xs.filter((x) => x.impact !== "Low");
    if (imp === "High+") xs = xs.filter((x) => x.impact === "High" || x.impact === "Critical");
    if (sent !== "All") xs = xs.filter((x) => x.sentiment.includes(sent));
    return xs;
  }, [q.data, cat, imp, sent]);
  const chains = useMemo(() => {
    const seen = new Set<string>();
    return (q.data?.items ?? []).filter((x) => x.chain.length > 1).filter((x) => {
      const k = x.chain.map((c) => c.node + c.direction).join(">");
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    }).slice(0, 8);
  }, [q.data]);

  return (
    <div className="space-y-5">
      <PageHeader title="뉴스 · 매크로 인텔리전스"
        description="여러 매체의 글로벌 시장 헤드라인을 중복 제거하고 (분야 · 논조 · 영향도) 분류한 뒤, 보도량이 아니라 예상 주가 영향도 순으로 정렬합니다." />
      <QueryState q={q} label="시장 뉴스" skeleton={<Section title="뉴스"><SkeletonBlock rows={10} /></Section>}>
        {(d) => (
          <>
            {d.method?.ai_error && <Pill tone="warn">{d.method.ai_error}</Pill>}
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
              <Section title="중요도순 헤드라인"
                subtitle={`기사 ${d.raw_count}건 → 고유 뉴스 ${d.unique_count}건 · ${(d.sources ?? []).join(", ")}`}
                right={<UpdatedAt at={d.fetched_at} />}>
                <div className="mb-3 space-y-2">
                  <div className="flex flex-wrap gap-1">
                    {["All", ...Object.keys(d.categories ?? {}).sort()].map((c) => (
                      <button key={c} onClick={() => setCat(c)} className={cn("rounded-md border px-2 py-0.5 text-xs", cat === c ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>
                        {c === "All" ? "전체" : categoryKo(c)}{c !== "All" && d.categories ? <span className="ml-1 text-muted-foreground">{d.categories[c]}</span> : null}
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Segmented size="xs" ariaLabel="영향도" value={imp} onChange={setImp} options={IMPACTS.map((v) => ({ value: v, label: IMPACT_LABEL[v] }))} />
                    <Segmented size="xs" ariaLabel="논조" value={sent} onChange={setSent} options={SENTS.map((v) => ({ value: v, label: SENT_LABEL[v] }))} />
                  </div>
                </div>
                {items.length === 0 ? <p className="text-sm text-muted-foreground">조건에 맞는 뉴스가 없습니다.</p> : <ul className="divide-y">{items.slice(0, 60).map((n) => <NewsRow key={n.id} n={n} />)}</ul>}
              </Section>
              <div className="space-y-4">
                <Section title="시장 요약">
                  {d.summary ? <SummaryBlock s={d.summary} /> : <p className="text-sm text-muted-foreground">요약을 불러올 수 없습니다</p>}
                </Section>
                <Section title="뉴스 → 매크로 → 섹터 → 종목" subtitle="오늘 주요 뉴스의 파급 경로 추정">
                  {chains.length === 0 ? <p className="text-sm text-muted-foreground">확인된 파급 경로가 없습니다.</p> : (
                    <ul className="space-y-3">
                      {chains.map((n) => (
                        <li key={n.id} className="text-xs">
                          <p className="mb-1 line-clamp-2 text-muted-foreground">{n.title}</p>
                          <ImpactChain chain={n.chain} />
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="mt-3 text-[10px] text-muted-foreground">{d.method?.method === "ai" ? `${d.method.model} 모델이 생성한 파급 경로입니다.` : "문서화된 규칙 템플릿 기반 파급 경로입니다 (AI 키를 설정하면 모델이 생성)."}</p>
                </Section>
              </div>
            </div>
          </>
        )}
      </QueryState>
    </div>
  );
}
