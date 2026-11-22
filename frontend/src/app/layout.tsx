import type { Metadata, Viewport } from "next";
import { Geist_Mono, Instrument_Serif } from "next/font/google";

import { Providers } from "@/components/providers";
import { AppShell } from "@/components/shell/app-shell";

import "./globals.css";

const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
// Display serif for the wordmark and hero numbers only; body text is Pretendard (Korean + Latin).
const display = Instrument_Serif({ variable: "--font-instrument-serif", subsets: ["latin"], weight: "400", style: ["normal", "italic"] });

const DESCRIPTION = "미국·한국 주식의 매크로, 뉴스, 기술적 분석과 과거 패턴 검증을 한곳에서.";
// Link previews (the thumbnail a shared link shows) only apply to the public demo - the local app has no public address.
const DEMO_URL = `${process.env.NEXT_PUBLIC_SITE_ORIGIN ?? "https://ahnhs4.github.io"}${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}`;
const PREVIEW = { title: "MarketLens — 미국·한국 주식 리서치 터미널", description: DESCRIPTION };

export const metadata: Metadata = {
  title: { default: "MarketLens", template: "%s · MarketLens" },
  description: DESCRIPTION,
  ...(process.env.NEXT_PUBLIC_STATIC === "1" ? {
    openGraph: { ...PREVIEW, type: "website", siteName: "MarketLens", locale: "ko_KR", url: `${DEMO_URL}/`,
      images: [{ url: `${DEMO_URL}/og.png`, width: 1200, height: 630, alt: "MarketLens 대시보드" }] },
    twitter: { ...PREVIEW, card: "summary_large_image", images: [`${DEMO_URL}/og.png`] },
  } : {}),
};

export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#0a0b0e" }, { color: "#f5f3ee" }],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" suppressHydrationWarning className={`${geistMono.variable} ${display.variable} h-full antialiased`}>
      <head>
        {/* Pretendard: the standard Korean UI face (variable weight, dynamic subset). */}
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="anonymous" />
        <link rel="stylesheet" crossOrigin="anonymous"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css" />
      </head>
      <body className="min-h-full">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
