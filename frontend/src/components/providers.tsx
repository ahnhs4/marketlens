"use client";

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { createContext, useContext, useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { api, put } from "@/lib/api";
import type { Settings } from "@/lib/types";

interface SettingsCtx {
  settings: Settings | null;
  update: (patch: Partial<Pick<Settings, "profile" | "weights" | "preferences">>) => Promise<void>;
}

const Ctx = createContext<SettingsCtx>({ settings: null, update: async () => {} });
export const useSettings = () => useContext(Ctx);

function SettingsProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings"), staleTime: 60_000 });
  useEffect(() => {
    const conv = data?.preferences?.color_convention ?? "global";
    document.documentElement.setAttribute("data-convention", conv);
  }, [data?.preferences?.color_convention]);
  const update: SettingsCtx["update"] = async (patch) => {
    const next = await put<Settings>("/api/settings", patch);
    qc.setQueryData(["settings"], next);
    // Scores depend on weights - refetch anything derived from them.
    qc.invalidateQueries({ predicate: (q) => ["scores", "technical", "reco", "dashboard"].includes(String(q.queryKey[0])) });
  };
  return <Ctx.Provider value={{ settings: data ?? null, update }}>{children}</Ctx.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: (count, err) => count < 1 && !(err as { status?: number })?.status?.toString().startsWith("4"),
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
        <TooltipProvider delay={150}>
          <SettingsProvider>{children}</SettingsProvider>
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
