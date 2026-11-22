"use client";

import { cn } from "cn";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/portfolio", label: "보유 현황" },
  { href: "/portfolio/plan", label: "배당 · 세금 · 리밸런싱" },
];

export function PortfolioTabs() {
  const path = usePathname();
  return (
    <nav className="mb-4 flex gap-1 border-b" aria-label="포트폴리오 화면">
      {TABS.map((t) => (
        <Link key={t.href} href={t.href} aria-current={path === t.href ? "page" : undefined}
          className={cn("-mb-px border-b-2 px-3 py-2 text-sm", path === t.href ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
