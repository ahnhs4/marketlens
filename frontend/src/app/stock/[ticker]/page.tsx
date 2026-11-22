import fs from "node:fs";
import path from "node:path";

import StockView from "./stock-view";

// The page itself is a client component (stock-view.tsx). This wrapper exists for the static demo build,
// which must list every ticker it pre-renders; the normal app renders any ticker on demand.
export function generateStaticParams(): { ticker: string }[] {
  if (process.env.NEXT_PUBLIC_STATIC !== "1") return [];
  const file = path.join(process.cwd(), "public", "snapshot", "manifest.json");
  const m = JSON.parse(fs.readFileSync(file, "utf8")) as { tickers: { symbol: string }[] };
  return m.tickers.map((t) => ({ ticker: t.symbol }));
}

export default function StockPage() {
  return <StockView />;
}
