import Link from "next/link";

export interface ExcludedGroup { key: string; label: string; rule: string; count: number; items: { symbol: string; name?: string | null; why?: string[] | null }[] }
export interface ExcludedOverbought { count: number; symbols: string[]; rule: string; pending?: number; groups?: ExcludedGroup[] }

/** The user's ranking exclusions (stochastic double overbought, RSI oversold zone), with the symbols left out. */
export function ExcludedNote({ e }: { e?: ExcludedOverbought | null }) {
  if (!e || (!e.count && !e.pending)) return null;
  const groups = (e.groups ?? []).filter((g) => g.count > 0);
  return (
    <details className="mb-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer">
        내 규칙으로 <b className="text-down">{e.count}종목</b> 순위에서 제외
        {groups.length > 0 && ` (${groups.map((g) => `${g.label} ${g.count}`).join(" · ")})`}
        {e.pending ? ` · ${e.pending}종목은 다음 스캔 후 적용` : ""}
      </summary>
      {groups.length ? groups.map((g) => (
        <div key={g.key} className="mt-2">
          <p><b className="font-medium text-foreground/80">{g.label}</b> — {g.rule}</p>
          <div className="mt-1 flex flex-wrap gap-1">{g.items.map((it) => (
            <Link key={it.symbol} href={`/stock/${it.symbol}`} title={it.why?.join(" · ") ?? it.name ?? undefined}
              className="rounded border border-down/30 px-1.5 py-0.5 text-down hover:bg-down/10">
              <span className="font-mono">{it.symbol}</span>{it.name && it.name !== it.symbol ? <span className="ml-1">{it.name}</span> : null}
            </Link>))}
            {g.count > g.items.length && <span className="px-1 py-0.5">외 {g.count - g.items.length}종목</span>}
          </div>
        </div>
      )) : (
        <>
          <p className="mt-1">{e.rule}</p>
          <div className="mt-1 flex flex-wrap gap-1">{e.symbols.map((s) => (
            <Link key={s} href={`/stock/${s}`} className="rounded border border-down/30 px-1.5 py-0.5 font-mono text-down hover:bg-down/10">{s}</Link>))}</div>
        </>
      )}
    </details>
  );
}
