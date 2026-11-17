"use client";

import { KeyRound, Loader2, ServerCog } from "lucide-react";
import { useState } from "react";
import { TokenProfilerApi } from "@/lib/api";
import { DEFAULT_API_KEY, DEFAULT_API_URL, normalizeApiUrl } from "@/lib/connection";
import { Logo } from "./logo";
import { useConnection } from "./providers";
import { Button, Field, Input } from "./ui/primitives";

export function ConnectScreen() {
  const { connect } = useConnection();
  const [apiUrl, setApiUrl] = useState(DEFAULT_API_URL);
  const [apiKey, setApiKey] = useState(DEFAULT_API_KEY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const conn = { apiUrl: normalizeApiUrl(apiUrl), apiKey: apiKey.trim() };
    try {
      await new TokenProfilerApi(conn).ping();
      connect(conn);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center text-center">
          <Logo size={44} />
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">Euron TokenProfiler</h1>
          <p className="mt-1.5 text-[13px] text-muted">Connect the console to your TokenProfiler API.</p>
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6" aria-label="Connect">
          <Field label="API URL">
            <div className="relative">
              <ServerCog className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle" />
              <Input value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} className="pl-9" placeholder="http://localhost:8380" required name="apiUrl" />
            </div>
          </Field>
          <Field label="API key" hint="Printed by `make up` and `make key`. Stored only in this browser.">
            <div className="relative">
              <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle" />
              <Input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" className="pl-9" placeholder="tp_…" required name="apiKey" autoComplete="off" />
            </div>
          </Field>
          {error && <p role="alert" className="rounded-lg border border-failed/30 bg-failed/10 px-3 py-2 text-[13px] text-failed">{error}</p>}
          <Button type="submit" variant="primary" className="w-full" disabled={busy || !apiKey.trim()}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            Connect
          </Button>
        </form>
      </div>
    </main>
  );
}
