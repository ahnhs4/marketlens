"use client";

import { Calculator, Database, Lock, Search } from "lucide-react";
import Link from "next/link";
import { useSyncExternalStore } from "react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

// Static demo only: on a visitor's first open, say plainly that this copy runs without the backend.
const KEY = "ml-demo-intro-seen";
const EVENT = "ml:demo-intro";
let dismissed = false;   // private mode, where localStorage throws: remember for this visit only

function subscribe(cb: () => void) {
  window.addEventListener(EVENT, cb);
  return () => window.removeEventListener(EVENT, cb);
}
function seen(): boolean {
  if (dismissed) return true;
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}
function markSeen() {
  dismissed = true;
  try { window.localStorage.setItem(KEY, "1"); } catch { /* private mode */ }
  window.dispatchEvent(new Event(EVENT));
}

const ROWS: { icon: typeof Database; label: string; demo: (asOf: string, n: number) => string; app: string }[] = [
  { icon: Database, label: "데이터", demo: (asOf) => `${asOf} 기준으로 고정`, app: "실시간 (Yahoo · OpenDART · FRED 등)" },
  { icon: Search, label: "종목", demo: (_, n) => `저장해 둔 ${n || "일부"}개 종목`, app: "미국 · 한국 전 종목" },
  { icon: Calculator, label: "계산", demo: () => "미리 계산해 둔 예시 결과", app: "조건을 바꾸면 바로 다시 계산" },
  { icon: Lock, label: "개인 화면", demo: () => "비공개", app: "포트폴리오 · 관심종목 · 매매 일지" },
];

const TOURS = [
  { href: "/", label: "대시보드" },
  { href: "/risk", label: "리스크 신호" },
  { href: "/backtest", label: "전략 빌더" },
  { href: "/stock/NVDA", label: "종목 분석 · NVDA" },
];

/** Opens by itself once per browser; `open`/`onOpenChange` let the demo bar reopen it. */
export function DemoIntro({ asOf, count, profile, open, onOpenChange }: {
  asOf?: string; count: number; profile: string; open: boolean; onOpenChange: (v: boolean) => void;
}) {
  const first = !useSyncExternalStore(subscribe, seen, () => true);   // the server render never opens it
  const close = () => { markSeen(); onOpenChange(false); };
  return (
    <Dialog open={open || first} onOpenChange={(v) => (v ? onOpenChange(true) : close())}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto break-keep p-0 sm:max-w-lg" showCloseButton={false}>
        <div className="border-b border-gold/20 bg-gradient-to-b from-gold/[0.10] to-transparent px-6 pb-5 pt-6">
          <div className="text-[10px] font-semibold tracking-[0.2em] text-gold">READ-ONLY DEMO</div>
          <DialogTitle className="mt-2 text-xl font-semibold tracking-tight">MarketLens 데모입니다</DialogTitle>
          <DialogDescription className="mt-2 text-[13px] leading-6 text-muted-foreground">
            실제 앱과 같은 코드, 같은 화면입니다. 다만 백엔드 서버 없이 GitHub Pages에 올린 읽기 전용 버전이라,
            <b className="font-semibold text-foreground"> {asOf ?? "특정 시점"} 기준 실데이터</b>를 저장해 두고 그대로 보여 줍니다.
          </DialogDescription>
        </div>

        <div className="px-6 py-5">
          <div className="overflow-hidden rounded-xl border">
            <div className="grid grid-cols-[6.25rem_1fr_1fr] bg-foreground/[0.03] text-[11px] font-medium text-muted-foreground">
              <span className="px-3 py-2" />
              <span className="px-3 py-2 text-gold">이 데모</span>
              <span className="px-3 py-2">실제 앱</span>
            </div>
            {ROWS.map(({ icon: Icon, label, demo, app }) => (
              <div key={label} className="grid grid-cols-[6.25rem_1fr_1fr] border-t text-[12px] leading-5">
                <span className="flex items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-muted-foreground"><Icon className="size-3.5 shrink-0" aria-hidden />{label}</span>
                <span className="px-3 py-2.5 font-medium">{demo(asOf ?? "특정 시점", count)}</span>
                <span className="px-3 py-2.5 text-muted-foreground">{app}</span>
              </div>
            ))}
          </div>

          <div className="mt-5 text-[11px] font-medium text-muted-foreground">먼저 둘러볼 곳</div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {TOURS.map((t) => (
              <Link key={t.href} href={t.href} onClick={close}
                className="rounded-full border px-3 py-1.5 text-[12px] transition-colors hover:border-gold/40 hover:bg-gold/[0.06] hover:text-gold">{t.label}</Link>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 border-t bg-foreground/[0.02] px-6 py-4">
          <a href={profile} className="text-[12px] text-muted-foreground hover:text-foreground">← 안현수 프로필</a>
          <button type="button" onClick={close} autoFocus
            className="rounded-full bg-gold px-5 py-2 text-[13px] font-semibold text-background transition-opacity hover:opacity-90">둘러보기</button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
