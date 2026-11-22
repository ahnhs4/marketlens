"use client";

import { cn } from "cn";
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import { BASE, STATIC, loadManifest, type Manifest } from "@/lib/static";

/** The stock a missing demo path asked for (/stock/SOXL/ -> SOXL), read after hydration; null on the server. */
function missingTicker(): string | null {
  if (!STATIC) return null;
  const { pathname } = window.location;
  const m = (pathname.startsWith(BASE) ? pathname.slice(BASE.length) : pathname).match(/^\/stock\/([^/]+)/);
  return m ? decodeURIComponent(m[1]).toUpperCase() : null;
}

export default function NotFound() {
  // GitHub Pages answers every missing path of the demo with this page (404.html), e.g. a stock it has no snapshot of
  const ticker = useSyncExternalStore(() => () => {}, missingTicker, () => null);
  const [demo, setDemo] = useState<Manifest["tickers"]>([]);
  useEffect(() => {
    if (ticker) loadManifest().then((x) => setDemo(x.tickers)).catch(() => {});
  }, [ticker]);

  if (ticker) {
    return (
      <div className="mx-auto mt-16 max-w-2xl rounded-2xl border bg-card p-8 text-center shadow-card">
        <div className="text-[11px] font-medium tracking-[0.18em] text-gold">데모에 없는 종목</div>
        <h1 className="mt-3 text-xl font-semibold">데모에 없는 종목입니다: <span className="font-mono">{ticker}</span></h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          이 데모는 미리 저장해 둔 {demo.length || ""}개 종목만 보여 줍니다. 실제 앱에서는 모든 미국·한국 종목을 조회할 수 있습니다.
        </p>
        {demo.length > 0 && (
          <div className="mt-6 flex flex-wrap justify-center gap-1.5">
            {demo.map((t) => (
              <Link key={t.symbol} href={`/stock/${encodeURIComponent(t.symbol)}`} title={`${t.symbol} · ${t.name}`}
                className={cn("rounded-full border px-2.5 py-1 text-[11px] hover:border-gold/40 hover:text-gold", t.market !== "KR" && "font-mono")}>
                {t.market === "KR" ? t.name_ko || t.name : t.symbol}
              </Link>
            ))}
          </div>
        )}
        <Link href="/" className="mt-6 inline-flex rounded-full border border-gold/40 px-4 py-2 text-sm font-medium text-gold hover:bg-gold/10">대시보드로</Link>
      </div>
    );
  }
  return (
    <div className="mx-auto mt-16 max-w-lg rounded-2xl border bg-card p-8 text-center shadow-card">
      <div className="text-[11px] font-medium tracking-[0.18em] text-gold">404</div>
      <h1 className="mt-3 text-xl font-semibold">페이지를 찾을 수 없습니다</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">주소가 바뀌었거나, 데모 스냅샷에 없는 종목일 수 있습니다.</p>
      <Link href="/" className="mt-5 inline-flex rounded-full border border-gold/40 px-4 py-2 text-sm font-medium text-gold hover:bg-gold/10">대시보드로</Link>
    </div>
  );
}
