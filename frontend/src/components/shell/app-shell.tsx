"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Bell, Bot, BriefcaseBusiness, Building2, Columns3, FlaskConical, Globe2, Landmark, LayoutDashboard, ListFilter, Menu, Moon, Newspaper, PiggyBank, Scale, Settings, Sparkles, Star, Sun, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import type { SessionStatus } from "@/lib/types";

import { SearchCommand } from "./search";

type NavItem = { href: string; label: string; icon: typeof Bell };
const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "마켓", items: [
    { href: "/", label: "대시보드", icon: LayoutDashboard },
    { href: "/market", label: "시장 개요", icon: Globe2 },
    { href: "/macro", label: "매크로", icon: Landmark },
    { href: "/news", label: "뉴스", icon: Newspaper },
  ] },
  { title: "분석", items: [
    { href: "/trend", label: "추세추종", icon: TrendingUp },
    { href: "/recommendations", label: "추천 종목", icon: Sparkles },
    { href: "/screener", label: "스크리너", icon: ListFilter },
    { href: "/backtest", label: "백테스트", icon: FlaskConical },
    { href: "/compare", label: "종목 비교", icon: Columns3 },
    { href: "/institutions", label: "기관 포트폴리오 (13F)", icon: Building2 },
    { href: "/valueup", label: "밸류업 · 저PBR", icon: Scale },
  ] },
  { title: "내 자산", items: [
    { href: "/watchlist", label: "관심종목", icon: Star },
    { href: "/portfolio", label: "포트폴리오", icon: BriefcaseBusiness },
    { href: "/portfolio/plan", label: "배당 · 세금 · 리밸런싱", icon: PiggyBank },
    { href: "/trading", label: "자동매매 (모의)", icon: Bot },
  ] },
  { title: "설정", items: [
    { href: "/alerts", label: "알림", icon: Bell },
    { href: "/settings", label: "설정", icon: Settings },
  ] },
];
const NAV = NAV_GROUPS.flatMap((g) => g.items);

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  // The most specific match wins, so /portfolio/plan doesn't also light up /portfolio.
  const current = NAV.map((n) => n.href).filter((h) => (h === "/" ? path === "/" : path === h || path.startsWith(`${h}/`)))
    .sort((a, b) => b.length - a.length)[0];
  return (
    <nav className="flex flex-col gap-5" aria-label="주 메뉴">
      {NAV_GROUPS.map((g) => (
        <div key={g.title}>
          <div className="mb-1.5 px-3 text-[10.5px] font-medium tracking-[0.16em] text-muted-foreground/70">{g.title}</div>
          <div className="flex flex-col gap-px">
            {g.items.map(({ href, label, icon: Icon }) => {
              const active = href === current;
              return (
                <Link key={href} href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
                  className={cn("group relative flex h-8 items-center gap-2.5 rounded-lg px-3 text-[13px] transition-colors",
                    active ? "bg-gradient-to-r from-gold/[0.14] via-gold/[0.05] to-transparent font-medium text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                  {active && <span className="absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-gold" aria-hidden />}
                  <Icon className={cn("size-[15px] shrink-0", active ? "text-gold" : "opacity-80 group-hover:opacity-100")} strokeWidth={1.75} aria-hidden />
                  <span className="truncate">{label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-3">
      <span className="relative grid size-8 place-items-center rounded-[10px] border border-gold/40 bg-gradient-to-br from-gold/25 via-gold/[0.06] to-transparent">
        <span className="font-display text-[19px] leading-none text-gold">M</span>
      </span>
      <span className="leading-none">
        <span className="font-display text-[21px] tracking-tight">Market<span className="gold-text italic">Lens</span></span>
        <span className="mt-1 block text-[9.5px] tracking-[0.2em] text-muted-foreground">US · KR EQUITY TERMINAL</span>
      </span>
    </Link>
  );
}

function SessionDot({ s, label }: { s?: SessionStatus; label: string }) {
  if (!s) return null;
  const color = s.state === "open" ? "bg-up-mark shadow-[0_0_0_3px] shadow-up-mark/20" : s.state === "closed" ? "bg-muted-foreground/50" : "bg-warn shadow-[0_0_0_3px] shadow-warn/20";
  const text = s.state === "open" ? "장중" : s.state === "pre" ? "프리마켓" : s.state === "post" ? "애프터마켓" : "장 마감";
  const t = new Date(s.local_time);
  const tz = label === "US" ? "America/New_York" : "Asia/Seoul";
  const hhmm = t.toLocaleTimeString("en-US", { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false });
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border bg-foreground/[0.02] px-3 py-1 text-[11.5px] text-muted-foreground" title={`${label === "US" ? "미국" : "한국"} 시장: ${text}`}>
      <span className={cn("size-1.5 rounded-full", color)} aria-hidden />
      <span className="font-medium text-foreground">{label === "US" ? "미국" : "한국"}</span> {text}
      <span className="tabular">{hhmm} {label === "US" ? "(뉴욕)" : "(서울)"}</span>
    </span>
  );
}

function AlertBell() {
  const qc = useQueryClient();
  const seen = useRef<Set<number>>(new Set());
  const primed = useRef(false);
  const { data } = useQuery({
    queryKey: ["alert-events-unread"],
    queryFn: () => api<{ id: number; symbol: string; message: string }[]>("/api/alerts/events?unread=true&limit=20"),
    refetchInterval: 60_000,
  });
  useEffect(() => {
    if (!data) return;
    for (const e of data) {
      if (seen.current.has(e.id)) continue;
      seen.current.add(e.id);
      if (primed.current) {  // only announce events that arrive while the app is open
        toast(`알림: ${e.symbol}`, { description: e.message });
        if (typeof Notification !== "undefined" && Notification.permission === "granted") new Notification(`MarketLens · ${e.symbol}`, { body: e.message });
      }
    }
    primed.current = true;
  }, [data]);
  const n = data?.length ?? 0;
  return (
    <Link href="/alerts" className="relative grid size-9 place-items-center rounded-full border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-accent hover:text-foreground" aria-label={`알림 (읽지 않음 ${n}건)`}
      onClick={() => qc.invalidateQueries({ queryKey: ["alert-events-unread"] })}>
      <Bell className="size-4" />
      {n > 0 && <span className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-gold px-1 text-center text-[10px] font-semibold leading-4 text-primary-foreground">{n}</span>}
    </Link>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // Icons switch via the .dark class, so server and client markup match without a mounted flag.
  return (
    <button onClick={() => setTheme(resolvedTheme === "light" ? "dark" : "light")}
      className="grid size-9 place-items-center rounded-full border border-transparent text-muted-foreground transition-colors hover:border-border hover:bg-accent hover:text-foreground" aria-label="라이트/다크 모드 전환">
      <Sun className="hidden size-4 dark:block" />
      <Moon className="size-4 dark:hidden" />
    </button>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobile, setMobile] = useState(false);
  const { data: snap } = useQuery({
    queryKey: ["sessions"],
    queryFn: () => api<{ sessions: { US: SessionStatus; KR: SessionStatus } }>("/api/market/overview"),
    refetchInterval: 60_000,
  });
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border/70 bg-sidebar py-5 lg:flex">
        <Brand />
        <div className="mt-7 min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          <NavList />
        </div>
        <p className="mx-4 rounded-lg border border-border/70 bg-foreground/[0.02] px-3 py-2.5 text-[10px] leading-relaxed text-muted-foreground">
          투자 판단 보조 도구이며 투자 권유가 아닙니다. 점수는 과거 데이터를 요약한 것이지 예측이 아닙니다.
        </p>
      </aside>
      <Sheet open={mobile} onOpenChange={setMobile}>
        <SheetContent side="left" className="w-64 bg-sidebar p-0 py-5">
          <SheetTitle className="sr-only">MarketLens 메뉴</SheetTitle>
          <Brand />
          <div className="mt-6 px-2"><NavList onNavigate={() => setMobile(false)} /></div>
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border/60 bg-background/70 px-4 backdrop-blur-xl backdrop-saturate-150 sm:px-8">
          <button className="grid size-9 place-items-center rounded-full border lg:hidden" onClick={() => setMobile(true)} aria-label="메뉴 열기"><Menu className="size-[18px]" /></button>
          <div className="min-w-0 flex-1"><SearchCommand /></div>
          <div className="hidden items-center gap-2 xl:flex">
            <SessionDot s={snap?.sessions?.US} label="US" />
            <SessionDot s={snap?.sessions?.KR} label="KR" />
          </div>
          <AlertBell />
          <ThemeToggle />
        </header>
        <main className="mx-auto w-full max-w-[1560px] flex-1 px-4 py-6 sm:px-8 sm:py-8">{children}</main>
      </div>
    </div>
  );
}
