"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader, Section, Segmented, SkeletonBlock } from "@/components/common";
import { useSettings } from "@/components/providers";
import { Slider } from "@/components/ui/slider";
import { api, post } from "@/lib/api";
import { bandKo, groupKo } from "@/lib/i18n";
import type { Settings } from "@/lib/types";

const SECTORS = ["Technology", "Semiconductors", "Software", "Communication Services", "Financials", "Health Care", "Consumer Discretionary",
  "Consumer Staples", "Energy", "Industrials", "Materials", "Utilities", "Real Estate", "Korea"];
const COMP_LABELS: Record<string, string> = { trend: "추세", momentum: "모멘텀", rsi: "RSI", volume: "거래량", breakout: "돌파",
  support_resistance: "지지/저항", volatility: "변동성", pattern: "패턴", timing: "진입 타이밍 (시장별 과거 데이터로 검증된 규칙)" };
const BLEND_KO: Record<string, string> = { technical: "기술적", macro: "매크로", fundamental: "펀더멘털" };
const THRESH_KO: Record<string, string> = { strong_buy: "강력 매수", buy: "매수", sell: "매도", strong_sell: "강력 매도" };
const IND_KO: Record<string, string> = {
  sma5: "5일선", sma20: "20일선", sma50: "50일선", sma60: "60일선", sma120: "120일선", sma200: "200일선", ema20: "EMA 20",
  bb: "볼린저 밴드", vwap: "VWAP", volume: "거래량", volume_profile: "매물대", levels: "지지/저항선",
  rsi: "RSI", macd: "MACD", stoch: "스토캐스틱", atr: "ATR", adx: "ADX", obv: "OBV",
};

function sliderVal(v: number | readonly number[]) { return (Array.isArray(v) ? v[0] : v) as number; }

function Account() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<{ email: string | null; local_mode: boolean; auth_required: boolean }>("/api/auth/me") });
  const [cred, setCred] = useState({ email: "", password: "" });
  const act = async (path: string) => {
    try {
      await post(path, cred);
      toast.success(path.endsWith("register") ? "계정을 만들었습니다" : "로그인했습니다");
      qc.invalidateQueries();
    } catch (e) { toast.error((e as Error).message); }
  };
  if (!me.data) return <SkeletonBlock rows={2} />;
  return (
    <div className="space-y-2 text-sm">
      <p>{me.data.local_mode ? "로컬 단일 사용자 모드 (AUTH_REQUIRED=false). 데이터는 로컬 사용자로 저장됩니다." : `${me.data.email} 계정으로 로그인됨`}</p>
      {me.data.local_mode ? (
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); act("/api/auth/login"); }}>
          <input type="email" placeholder="이메일" value={cred.email} onChange={(e) => setCred({ ...cred, email: e.target.value })} className="h-8 rounded-md border bg-background px-2 text-sm" autoComplete="username" />
          <input type="password" placeholder="비밀번호 (8자 이상)" value={cred.password} onChange={(e) => setCred({ ...cred, password: e.target.value })} className="h-8 rounded-md border bg-background px-2 text-sm" autoComplete="current-password" />
          <button className="h-8 rounded-md border px-3 text-sm hover:bg-accent">로그인</button>
          <button type="button" onClick={() => act("/api/auth/register")} className="h-8 rounded-md border px-3 text-sm hover:bg-accent">계정 만들기</button>
        </form>
      ) : <button onClick={async () => { await post("/api/auth/logout"); qc.invalidateQueries(); }} className="h-8 rounded-md border px-3 text-sm hover:bg-accent">로그아웃</button>}
      <p className="text-xs text-muted-foreground">세션은 HttpOnly · SameSite=Lax 서명 쿠키를 쓰며, 비밀번호는 scrypt로 해시 저장됩니다.</p>
    </div>
  );
}

export default function SettingsPage() {
  const { settings, update } = useSettings();
  const [draft, setS] = useState<Settings | null>(null);
  const s = draft ?? settings;
  if (!s) return <SkeletonBlock rows={10} />;
  const tc = s.weights.technical_components;
  const tcTotal = Object.values(tc).reduce((a, b) => a + b, 0) || 1;
  const save = async () => {
    try {
      await update({ profile: s.profile, weights: s.weights, preferences: s.preferences });
      setS(null);
      toast.success("설정을 저장했습니다 - 점수가 다시 계산됩니다");
    } catch (e) { toast.error((e as Error).message); }
  };
  const P = s.profile, W = s.weights, Pr = s.preferences;
  const setP = (patch: Partial<Settings["profile"]>) => setS({ ...s, profile: { ...P, ...patch } });
  const setW = (patch: Partial<Settings["weights"]>) => setS({ ...s, weights: { ...W, ...patch } });
  const setPr = (patch: Partial<Settings["preferences"]>) => setS({ ...s, preferences: { ...Pr, ...patch } });

  return (
    <div className="space-y-5">
      <PageHeader title="설정" description="투자 성향과 점수 엔진이 쓰는 모든 가중치." right={<button onClick={save} className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground">변경 사항 저장</button>} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="투자 성향" subtitle="대시보드(관심 시장 · 섹터)와 포트폴리오 위험 점검에 반영">
          <div className="space-y-3 text-sm">
            <Row label="위험 성향"><Segmented value={P.risk_tolerance} onChange={(v) => setP({ risk_tolerance: v })} options={[["Conservative", "안정형"], ["Moderate", "중립형"], ["Aggressive", "공격형"]].map(([value, label]) => ({ value, label }))} /></Row>
            <Row label="선호 보유 기간"><Segmented value={P.holding_period} onChange={(v) => setP({ holding_period: v })} options={[["Short", "단기"], ["Medium", "중기"], ["Long", "장기"]].map(([value, label]) => ({ value, label }))} /></Row>
            <Row label="선호 시장"><Segmented value={P.preferred_market} onChange={(v) => setP({ preferred_market: v })} options={[["US", "미국"], ["Korea", "한국"], ["Global", "글로벌"]].map(([value, label]) => ({ value, label }))} /></Row>
            <Row label="기준 통화"><Segmented value={P.base_currency} onChange={(v) => setP({ base_currency: v })} options={["USD", "KRW"].map((x) => ({ value: x, label: x }))} /></Row>
            <Row label={`허용 가능한 최대 낙폭: ${(P.max_drawdown * 100).toFixed(0)}%`}>
              <div className="w-56"><Slider value={[P.max_drawdown * 100]} min={5} max={60} step={1} onValueChange={(v) => setP({ max_drawdown: sliderVal(v) / 100 })} /></div>
            </Row>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">선호 섹터</div>
              <div className="flex flex-wrap gap-1">{SECTORS.map((x) => {
                const on = P.preferred_sectors.includes(x);
                return <button key={x} onClick={() => setP({ preferred_sectors: on ? P.preferred_sectors.filter((y) => y !== x) : [...P.preferred_sectors, x] })}
                  className={cn("rounded-md border px-2 py-0.5 text-xs", on ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground")} aria-pressed={on}>{groupKo(x)}</button>;
              })}</div>
            </div>
          </div>
        </Section>
        <Section title="점수 혼합 비율" subtitle="종합 점수 = 가중 평균 · 종합 순위 = 기술적 vs 매크로">
          <div className="space-y-4 text-sm">
            {(["technical", "macro", "fundamental"] as const).map((k) => (
              <Row key={k} label={`종합 점수 · ${BLEND_KO[k]} ${(W.overall_blend[k] * 100).toFixed(0)}%`}>
                <div className="w-56"><Slider value={[W.overall_blend[k] * 100]} min={0} max={100} step={5} onValueChange={(v) => setW({ overall_blend: { ...W.overall_blend, [k]: sliderVal(v) / 100 } })} /></div>
              </Row>
            ))}
            <Row label={`종합 순위 · 기술적 ${(W.combined_ranking.technical * 100).toFixed(0)}% / 매크로 ${((1 - W.combined_ranking.technical) * 100).toFixed(0)}%`}>
              <div className="w-56"><Slider value={[W.combined_ranking.technical * 100]} min={0} max={100} step={5} onValueChange={(v) => setW({ combined_ranking: { technical: sliderVal(v) / 100, macro: 1 - sliderVal(v) / 100 } })} /></div>
            </Row>
            <p className="text-xs text-muted-foreground">종목별로 계산 가능한 점수만으로 가중치를 다시 정규화합니다.</p>
          </div>
        </Section>
        <Section title="채점 방식 (진입 타이밍)" subtitle="종목 점수 · 투자 의견 · 추천 · 전망 문장 전체에 적용. 바꾸면 모든 점수를 다시 계산합니다.">
          <div className="space-y-3 text-sm">
            <Segmented value={Pr.timing_style ?? "user"} onChange={(v) => setPr({ timing_style: v })}
              options={[{ value: "user", label: "내 규칙 (기본)" }, { value: "evidence", label: "데이터 검증" }]} />
            {(Pr.timing_style ?? "user") === "user" ? (
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li>· 단기 · 중기 · 장기 RSI 과매수는 크게 감점 (70~80: −6/−8/−10, 80+: −9/−11/−13), 과매도는 같은 크기로 가산. 2~3개 기간이 겹치면 −6/−12 추가, 과매수에서 꺾이면 −5, 과매도에서 반등하면 +6.</li>
                <li>· 52주 고점 2% 이내 −15 · 5% 이내 −10 · 10% 이내 −5, 역대 고점권 −5 추가, 실제 저항선 3% 이내 −8. 고점 돌파는 가산하지 않음.</li>
                <li>· 골든크로스 5/20 +6 · 20/60 +10 · 50/200 +14 (데드크로스는 반대), 20/60 · 50/200 골든크로스 임박 +6.</li>
                <li>· 진입 타이밍 비중: 기술 점수의 35%, 투자 의견 판단 점수의 45%.</li>
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">이 앱의 과거 데이터로 이후 성과가 확인된 규칙만 시장별로 반영합니다 (한국: 급등 이격 · 과매수 감점, 강한 종목의 조정 가산 / 미국: 모멘텀 가산). 미국에서는 과매수 · 고점이 이후 성과를 나쁘게 하지 않았습니다.</p>
            )}
          </div>
        </Section>
        <Section title="기술적 점수 가중치" subtitle="점수 = 50 + Σ 가중치 × (구성 점수 − 50). 가중치 합은 100%로 정규화됩니다. '내 규칙'에서 기본값을 그대로 두면 타이밍 35% · 돌파 0% 세트를 씁니다.">
          <div className="space-y-2.5 text-sm">
            {Object.entries(tc).map(([k, v]) => (
              <Row key={k} label={`${COMP_LABELS[k] ?? k} · ${((v / tcTotal) * 100).toFixed(0)}%`}>
                <div className="w-56"><Slider value={[v * 100]} min={0} max={40} step={1} onValueChange={(x) => setW({ technical_components: { ...tc, [k]: sliderVal(x) / 100 } })} /></div>
              </Row>
            ))}
            <button className="text-xs text-primary hover:underline" onClick={() => setW({ technical_components: { ...s.reference.default_weights.technical_components } })}>기본값으로 되돌리기</button>
            <p className="text-xs text-muted-foreground">시장 국면 조정이 추가로 적용됩니다: 위험선호 국면에서는 모멘텀/돌파 ×1.25, 위험회피 국면에서는 매수 기준점 +6점.</p>
          </div>
        </Section>
        <Section title="화면 · 차트">
          <div className="space-y-3 text-sm">
            <Row label="상승/하락 색상"><Segmented value={Pr.color_convention} onChange={(v) => setPr({ color_convention: v })} options={[{ value: "korea", label: "한국식 (상승 빨강)" }, { value: "global", label: "해외식 (상승 초록)" }]} /></Row>
            <Row label="RSI 기간 (단기 · 중기 · 장기)">
              <div className="flex items-center gap-1.5 text-xs">
                {([["rsi_short_period", "단기", 50], ["rsi_period", "중기", 50], ["rsi_long_period", "장기", 100]] as const).map(([k, l, max]) => (
                  <label key={k} className="inline-flex items-center gap-1 text-muted-foreground">{l}
                    <input type="number" min={2} max={max} value={Pr[k]} onChange={(e) => setPr({ [k]: Number(e.target.value) })} className="h-8 w-14 rounded-md border bg-background px-2 text-sm text-foreground" />
                  </label>
                ))}
              </div>
            </Row>
            <Row label="기본 차트 기간"><Segmented size="xs" value={Pr.default_range} onChange={(v) => setPr({ default_range: v })} options={[["3M", "3개월"], ["6M", "6개월"], ["1Y", "1년"], ["3Y", "3년"], ["5Y", "5년"]].map(([value, label]) => ({ value, label }))} /></Row>
            <Row label="매물대 기간"><Segmented size="xs" value={Pr.volume_profile_period} onChange={(v) => setPr({ volume_profile_period: v })} options={[["1M", "1개월"], ["3M", "3개월"], ["6M", "6개월"], ["1Y", "1년"], ["3Y", "3년"]].map(([value, label]) => ({ value, label }))} /></Row>
            <div>
              <div className="mb-1 text-xs text-muted-foreground">기본으로 표시할 보조지표</div>
              <div className="flex flex-wrap gap-1">{Object.entries(Pr.indicators).map(([k, on]) => (
                <button key={k} onClick={() => setPr({ indicators: { ...Pr.indicators, [k]: !on } })} aria-pressed={on}
                  className={cn("rounded-md border px-2 py-0.5 text-xs", on ? "border-primary bg-primary/10" : "text-muted-foreground")}>{IND_KO[k] ?? k.toUpperCase()}</button>
              ))}</div>
            </div>
          </div>
        </Section>
        <Section title="참고: 신호 기준점 · 점수 구간" subtitle="README → 점수 산출 방법에 정리">
          <div className="grid gap-4 text-xs sm:grid-cols-2">
            <table className="tabular"><tbody>{Object.entries(s.reference.signal_thresholds).map(([k, v]) => <tr key={k} className="border-b"><td className="py-1 pr-4 text-muted-foreground">{THRESH_KO[k] ?? k}</td><td>{k.includes("sell") ? "≤" : "≥"} {v}</td></tr>)}</tbody></table>
            <table className="tabular"><tbody>{s.reference.score_bands.map(([lo, name]) => <tr key={name} className="border-b"><td className="py-1 pr-4 text-muted-foreground">{lo}점 이상</td><td>{bandKo(name)}</td></tr>)}</tbody></table>
          </div>
        </Section>
        <Section title="계정"><Account /></Section>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted-foreground">{label}</span>{children}</div>;
}
