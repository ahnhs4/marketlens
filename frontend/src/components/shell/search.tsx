"use client";

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { STATIC, loadManifest } from "@/lib/static";

interface Match { symbol: string; name: string; name_ko?: string | null; exchange?: string | null; market?: string | null }

export function SearchCommand() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Match[]>([]);
  const [loading, setLoading] = useState(false);
  const [demo, setDemo] = useState<Match[]>([]);   // static demo: every stock it carries, listed before typing
  const router = useRouter();

  useEffect(() => {
    if (STATIC && open && !demo.length) loadManifest().then((m) => setDemo(m.tickers)).catch(() => {});
  }, [open, demo.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !(e.target as HTMLElement)?.closest("input,textarea"))) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!q.trim()) return;
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      setLoading(true);
      try {
        if (STATIC) {   // the demo can only open the stocks it has a snapshot of
          const m = await loadManifest();
          const n = q.trim().toLowerCase().replace(/\s/g, "");
          setResults(m.tickers.filter((t) => [t.symbol, t.name, t.name_ko ?? ""].some((x) => x.toLowerCase().replace(/\s/g, "").includes(n))).slice(0, 10));
          return;
        }
        const r = await api<{ results: Match[] }>(`/api/search?q=${encodeURIComponent(q)}&limit=10`, { signal: ctl.signal });
        setResults(r.results);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 160);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [q]);

  const shown = q.trim() ? results : STATIC ? demo : [];
  const go = (sym: string) => {
    setOpen(false);
    setQ("");
    router.push(`/stock/${encodeURIComponent(sym)}`);
  };

  return (
    <>
      <button onClick={() => setOpen(true)}
        className="flex h-9 w-full max-w-md items-center gap-2.5 rounded-full border bg-foreground/[0.03] px-4 text-[13px] text-muted-foreground transition-colors hover:border-gold/30 hover:bg-foreground/[0.05]"
        aria-label="종목 검색">
        <Search className="size-4" aria-hidden />
        <span className="truncate">종목명·티커·종목코드 검색 — 삼성전자, 엔비디아, NVDA, 005930…</span>
        <kbd className="ml-auto hidden rounded-md border bg-card px-1.5 py-0.5 font-mono text-[10px] sm:inline">⌘K</kbd>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="top-[20%] translate-y-0 overflow-hidden p-0 sm:max-w-lg" showCloseButton={false}>
          <DialogTitle className="sr-only">종목 검색</DialogTitle>
          <Command shouldFilter={false}>
            <CommandInput placeholder="종목명, 티커 또는 종목코드" value={q} onValueChange={setQ} autoFocus
              onKeyDown={(e) => {
                // the demo has no page for a stock it does not carry (GitHub Pages would answer 404)
                if (e.key === "Enter" && shown.length === 0 && q.trim() && !STATIC) go(q.trim().toUpperCase());
              }} />
            <CommandList className="max-h-80">
              {q && !loading && (STATIC
                ? <CommandEmpty>데모에 없는 종목입니다: “{q.toUpperCase()}”. 데모는 미리 저장해 둔 {demo.length || ""}개 종목만 보여 줍니다.</CommandEmpty>
                : <CommandEmpty>일치하는 종목이 없습니다. Enter를 누르면 “{q.toUpperCase()}”로 조회합니다.</CommandEmpty>)}
              {shown.length > 0 && (
                <CommandGroup heading={STATIC && !q.trim() ? `데모에 들어 있는 종목 ${demo.length}개` : "종목"}>
                  {shown.map((r) => (
                    <CommandItem key={r.symbol} value={r.symbol} onSelect={() => go(r.symbol)} className="flex items-center gap-3">
                      <span className="w-24 shrink-0 font-mono text-xs font-semibold">{r.symbol}</span>
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {r.name_ko && r.name_ko !== r.name ? `${r.name_ko} · ` : ""}{r.name}
                      </span>
                      <span className="shrink-0 text-[10px] text-muted-foreground">{r.exchange ?? r.market}</span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
            </CommandList>
          </Command>
        </DialogContent>
      </Dialog>
    </>
  );
}
