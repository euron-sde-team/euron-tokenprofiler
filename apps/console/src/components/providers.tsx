"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { TokenProfilerApi } from "@/lib/api";
import {
  clearConnection, DEFAULT_API_KEY, DEFAULT_API_URL, loadConnection, saveConnection, type Connection,
} from "@/lib/connection";

// ── connection ───────────────────────────────────────────────────────────────
interface ConnectionCtx {
  ready: boolean;
  conn: Connection | null;
  api: TokenProfilerApi | null;
  connect: (c: Connection) => void;
  disconnect: () => void;
}

const Ctx = createContext<ConnectionCtx | null>(null);

export function useConnection(): ConnectionCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useConnection outside <Providers>");
  return v;
}

/** The authenticated API client. Only call on screens rendered behind the connect gate. */
export function useApi(): TokenProfilerApi {
  const { api } = useConnection();
  if (!api) throw new Error("useApi without a connection");
  return api;
}

function ConnectionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [conn, setConn] = useState<Connection | null>(null);
  const qc = useQueryClient();

  useEffect(() => {
    const saved = loadConnection();
    if (saved) setConn(saved);
    else if (DEFAULT_API_KEY) setConn({ apiUrl: DEFAULT_API_URL, apiKey: DEFAULT_API_KEY });
    setReady(true);
  }, []);

  const connect = useCallback((c: Connection) => {
    saveConnection(c);
    setConn(c);
  }, []);
  const disconnect = useCallback(() => {
    clearConnection();
    setConn(null);
    qc.clear();
  }, [qc]);

  const api = useMemo(() => (conn ? new TokenProfilerApi(conn) : null), [conn]);
  const value = useMemo(() => ({ ready, conn, api, connect, disconnect }), [ready, conn, api, connect, disconnect]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// ── toasts ───────────────────────────────────────────────────────────────────
type ToastKind = "success" | "error" | "info";
interface Toast { id: number; kind: ToastKind; message: string }
const ToastCtx = createContext<(kind: ToastKind, message: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: ToastKind, message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);
  const tone: Record<ToastKind, string> = {
    success: "border-completed/40", error: "border-failed/50", info: "border-line",
  };
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`pointer-events-auto animate-slideIn rounded-lg border bg-raised px-3.5 py-2.5 text-[13px] shadow-pop ${tone[t.kind]}`}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// ── root ─────────────────────────────────────────────────────────────────────
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 2_000,
            refetchOnWindowFocus: false,
            retry: (count, err) => !(err instanceof Error && "status" in err && [401, 403, 404].includes(err.status as number)) && count < 2,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ConnectionProvider>{children}</ConnectionProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
