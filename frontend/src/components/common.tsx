"use client";

import { cn } from "cn";
import {
  AlertTriangle, ArrowDown, ArrowDownRight, ArrowUp, ArrowUpRight, ChevronsDown, ChevronsUp, Clock, HelpCircle, Minus,
  RefreshCw, Zap,
} from "lucide-react";
import type { ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { ago, isNum, pct, timeET, timeKST } from "@/lib/format";
import { bandKo, impactKo, sentimentKo, signalKo, viewKo } from "@/lib/i18n";
import type { Component } from "@/lib/types";

// ------------------------------------------------------------------ layout
export function Section({ title, subtitle, right, children, className, bodyClassName, id }: {
  title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; id?: string;
}) {
  return (
    <section id={id} className={cn("rounded-xl border bg-card text-card-foreground shadow-card", className)}>
      {(title || right) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border/70 px-5 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="flex items-center gap-2 text-[13.5px] font-semibold tracking-tight">
              <span className="h-3.5 w-[3px] shrink-0 rounded-full bg-gradient-to-b from-gold to-gold/30" aria-hidden />{title}</h2>}
            {subtitle && <p className="mt-1 pl-[11px] text-xs leading-relaxed text-muted-foreground">{subtitle}</p>}
          </div>
          {right && <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">{right}</div>}
        </header>
      )}
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, right }: { title: string; description?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.025em]">{title}</h1>
        <div className="mt-2.5 h-px w-12 bg-gradient-to-r from-gold to-transparent" aria-hidden />
        {description && <p className="mt-2.5 max-w-3xl text-[13.5px] leading-relaxed text-muted-foreground">{description}</p>}
      </div>
      {right}
    </div>
  );
}

// -------------------------------------------------------------- data state
export function QueryState<T>({ q, children, skeleton, label = "data" }: {
  q: { isLoading: boolean; isError: boolean; error: unknown; data: T | undefined; refetch: () => unknown; isFetching?: boolean };
  children: (data: T) => ReactNode; skeleton?: ReactNode; label?: string;
}) {
  if (q.isLoading) return <>{skeleton ?? <SkeletonBlock />}</>;
  if (q.isError || q.data === undefined) {
    const e = q.error as ApiError | undefined;
    return <Unavailable message={e?.message} onRetry={() => q.refetch()} label={label} />;
  }
  return <>{children(q.data)}</>;
}

export function Unavailable({ message, onRetry, label }: { message?: string; onRetry?: () => void; label?: string }) {
  return (
    <div role="status" className="flex flex-col items-start gap-2 rounded-xl border border-dashed bg-muted/20 px-4 py-4 text-sm text-muted-foreground">
      <span className="flex items-center gap-2"><AlertTriangle className="size-4 text-warn" aria-hidden />데이터를 불러올 수 없습니다 (Data unavailable){label ? ` — ${label}` : ""}</span>
      {message && !message.startsWith("데이터를 불러올 수 없습니다") && <span className="text-xs">{message}</span>}
      {onRetry && (
        <button onClick={onRetry} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
          <RefreshCw className="size-3" /> 다시 시도
        </button>
      )}
    </div>
  );
}

export function SkeletonBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)} aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className={cn("h-4", i === 0 ? "w-2/3" : "w-full")} />)}
    </div>
  );
}

// ------------------------------------------------------------ numbers
export function Delta({ value, digits = 2, className, frac = false, showIcon = true }: { value: unknown; digits?: number; className?: string; frac?: boolean; showIcon?: boolean }) {
  const v = isNum(value) ? (frac ? value * 100 : value) : null;
  if (v === null) return <span className={cn("text-muted-foreground", className)}>—</span>;
  const Icon = v > 0 ? ArrowUpRight : v < 0 ? ArrowDownRight : Minus;
  return (
    <span className={cn("inline-flex items-center gap-0.5 tabular", v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground", className)}>
      {showIcon && <Icon className="size-3.5 shrink-0" aria-hidden />}
      {pct(v, digits)}
    </span>
  );
}

export function Stat({ label, value, sub, className }: { label: ReactNode; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="truncate text-[11px] tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold tabular">{value}</div>
      {sub && <div className="truncate text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

// --------------------------------------------------------------- signals
const SIGNAL_STYLE: Record<string, { cls: string; Icon: typeof ArrowUp }> = {
  "STRONG BUY": { cls: "bg-up/[0.13] text-up border-up/35", Icon: ChevronsUp },
  BUY: { cls: "bg-up/[0.08] text-up border-up/25", Icon: ArrowUp },
  NEUTRAL: { cls: "bg-muted/60 text-muted-foreground border-border", Icon: Minus },
  SELL: { cls: "bg-down/[0.08] text-down border-down/25", Icon: ArrowDown },
  "STRONG SELL": { cls: "bg-down/[0.13] text-down border-down/35", Icon: ChevronsDown },
};

export function SignalBadge({ signal, size = "md" }: { signal?: string | null; size?: "sm" | "md" | "lg" }) {
  const key = (signal ?? "NEUTRAL").toUpperCase().replace("STRONG BUY", "STRONG BUY");
  const st = SIGNAL_STYLE[key] ?? SIGNAL_STYLE.NEUTRAL;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full border font-semibold tracking-wide", st.cls,
      size === "lg" ? "px-3 py-1 text-sm" : size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-0.5 text-xs")}>
      <st.Icon className={size === "lg" ? "size-4" : "size-3"} aria-hidden />
      {signalKo(key)}
    </span>
  );
}

export function ViewBadge({ view }: { view: string }) {
  const map: Record<string, string> = { Bullish: "STRONG BUY", Bearish: "STRONG SELL", Neutral: "NEUTRAL" };
  const st = SIGNAL_STYLE[map[view] ?? "NEUTRAL"];
  return <span className={cn("inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", st.cls)}><st.Icon className="size-3" aria-hidden />{viewKo(view)}</span>;
}

export function scoreTone(s: unknown) {
  if (!isNum(s)) return "text-muted-foreground";
  return s >= 62 ? "text-up" : s <= 40 ? "text-down" : "text-foreground";
}

export function ScoreMeter({ score, label, className }: { score: unknown; label?: string; className?: string }) {
  const v = isNum(score) ? Math.max(0, Math.min(100, score)) : null;
  return (
    <div className={cn("w-full", className)} aria-label={label ? `${label} ${v ?? "unavailable"}` : undefined}>
      <div className="relative h-1 w-full overflow-hidden rounded-full bg-foreground/[0.07]">
        <div className="absolute inset-y-0 left-1/2 z-10 w-px bg-background/80" aria-hidden />
        {v !== null && (
          <div className={cn("h-full rounded-full bg-gradient-to-r", v >= 62 ? "from-up-mark/50 to-up-mark" : v <= 40 ? "from-down-mark/50 to-down-mark" : "from-gold/30 to-gold/80")} style={{ width: `${v}%` }} />
        )}
      </div>
    </div>
  );
}

export function ScoreTile({ label, score, band, why, sub }: { label: string; score: unknown; band?: string | null; why?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border bg-gradient-to-b from-foreground/[0.025] to-transparent p-3.5 hairline-top">
      <div className="flex items-center justify-between text-[11px] tracking-wide text-muted-foreground">
        <span>{label}</span>
        {why}
      </div>
      <div className="mt-1.5 flex items-baseline gap-2">
        <span className={cn("text-[28px] font-semibold leading-none tracking-tight tabular", scoreTone(score))}>{isNum(score) ? score.toFixed(0) : "—"}</span>
        <span className="text-xs text-muted-foreground">/100{band ? ` · ${bandKo(band)}` : ""}</span>
      </div>
      <ScoreMeter score={score} className="mt-3" />
      {sub && <div className="mt-1.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function WhyButton({ components, title = "점수 산출 근거", formula, extra }: { components: Component[]; title?: string; formula?: string; extra?: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger className="inline-flex items-center gap-0.5 rounded px-1 text-[11px] normal-case tracking-normal text-primary hover:bg-primary/10" aria-label={title}>
        <HelpCircle className="size-3" aria-hidden /> 근거
      </PopoverTrigger>
      <PopoverContent className="w-96 max-w-[90vw] p-0" align="end">
        <div className="border-b px-3 py-2 text-sm font-medium">{title}</div>
        <ul className="max-h-96 divide-y overflow-auto text-xs">
          {components.map((c) => (
            <li key={c.key} className="px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{c.label}</span>
                <span className="flex items-center gap-2 tabular">
                  <span className="text-muted-foreground">{c.score.toFixed(0)}</span>
                  {isNum(c.points) && (
                    <span className={cn("w-12 text-right font-semibold", c.points > 0 ? "text-up" : c.points < 0 ? "text-down" : "text-muted-foreground")}>
                      {c.points > 0 ? "+" : ""}{c.points.toFixed(1)}
                    </span>
                  )}
                </span>
              </div>
              {c.details?.filter(Boolean).length > 0 && (
                <ul className="mt-1 space-y-0.5 text-muted-foreground">{c.details.filter(Boolean).map((d, i) => <li key={i}>· {d}</li>)}</ul>
              )}
            </li>
          ))}
        </ul>
        {(formula || extra) && <div className="border-t px-3 py-2 text-[11px] text-muted-foreground">{extra}{formula}</div>}
      </PopoverContent>
    </Popover>
  );
}

// ------------------------------------------------------------- freshness
export function Freshness({ delay, quoteTime, updatedAt, source }: { delay?: number | null; quoteTime?: string | null; updatedAt?: string | null; source?: string | null }) {
  const realtime = delay === 0;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
      {delay !== undefined && delay !== null && (
        <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium",
          realtime ? "border-up/30 bg-up/[0.06] text-up" : "border-warn/35 bg-warn/[0.06] text-warn")}>
          {realtime ? <Zap className="size-3" aria-hidden /> : <Clock className="size-3" aria-hidden />}
          {realtime ? "실시간" : `${delay}분 지연`}
        </span>
      )}
      {quoteTime && <span title={timeET(quoteTime)}>시세 {timeKST(quoteTime)}</span>}
      {updatedAt && <span title={timeKST(updatedAt)}>{ago(updatedAt)} 업데이트</span>}
      {source && <span className="hidden sm:inline">· {source}</span>}
    </span>
  );
}

export function UpdatedAt({ at, source }: { at?: string | null; source?: string }) {
  if (!at) return null;
  return <span title={`${timeKST(at)} · ${timeET(at)}`}>{source ? `${source} · ` : ""}{ago(at)} 업데이트</span>;
}

// ------------------------------------------------------------- sparkline
export function Sparkline({ data, className, positive }: { data: number[]; className?: string; positive?: boolean }) {
  if (!data || data.length < 2) return <div className={cn("h-8", className)} />;
  const w = 100, h = 30;
  const min = Math.min(...data), max = Math.max(...data);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / (max - min || 1)) * (h - 4) - 2}`).join(" ");
  const up = positive ?? data[data.length - 1] >= data[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-8 w-full", className)} aria-hidden>
      <polyline points={pts} fill="none" strokeWidth={1.5} vectorEffect="non-scaling-stroke"
        className={up ? "stroke-up-mark" : "stroke-down-mark"} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// ------------------------------------------------------------- news badges
export function SentimentBadge({ s }: { s: string }) {
  const pos = s.includes("Bullish"), neg = s.includes("Bearish"), strong = s.startsWith("Very");
  const Icon = pos ? (strong ? ChevronsUp : ArrowUp) : neg ? (strong ? ChevronsDown : ArrowDown) : Minus;
  return (
    <span className={cn("inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
      pos ? "border-up/30 text-up" : neg ? "border-down/30 text-down" : "border-border text-muted-foreground")}>
      <Icon className="size-3" aria-hidden />{sentimentKo(s)}
    </span>
  );
}

export function ImpactBadge({ i }: { i: string }) {
  const cls = i === "Critical" ? "border-down-mark bg-down-mark text-white" : i === "High" ? "bg-warn/[0.12] text-warn border-warn/35" : i === "Medium" ? "bg-muted/60 text-foreground" : "text-muted-foreground";
  return <span className={cn("inline-flex rounded-full border px-2 py-0.5 text-[10px] font-medium", cls)}>영향 {impactKo(i)}</span>;
}

// ------------------------------------------------------------- segmented
export function Segmented<T extends string>({ value, onChange, options, size = "sm", ariaLabel }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[]; size?: "xs" | "sm"; ariaLabel?: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex flex-wrap gap-0.5 rounded-full border bg-foreground/[0.03] p-[3px]">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)}
          className={cn("rounded-full font-medium transition-all duration-150", size === "xs" ? "px-2.5 py-0.5 text-[11px]" : "px-3 py-1 text-xs",
            value === o.value ? "bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.12)] ring-1 ring-gold/40" : "text-muted-foreground hover:text-foreground")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Pill({ children, tone = "default", className }: { children: ReactNode; tone?: "default" | "up" | "down" | "warn" | "primary"; className?: string }) {
  const t = { default: "border-border bg-foreground/[0.03] text-muted-foreground", up: "border-up/25 bg-up/[0.07] text-up",
    down: "border-down/25 bg-down/[0.07] text-down", warn: "border-warn/30 bg-warn/[0.07] text-warn",
    primary: "border-gold/35 bg-gold/[0.08] text-gold" }[tone];
  return <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium", t, className)}>{children}</span>;
}

export function ReasonList({ items, tone, empty = "없음" }: { items: string[]; tone: "up" | "down"; empty?: string }) {
  if (!items?.length) return <p className="text-xs text-muted-foreground">{empty}</p>;
  const Icon = tone === "up" ? ArrowUpRight : ArrowDownRight;
  return (
    <ul className="space-y-1.5 text-sm">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2">
          <Icon className={cn("mt-0.5 size-4 shrink-0", tone === "up" ? "text-up" : "text-down")} aria-hidden />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}
