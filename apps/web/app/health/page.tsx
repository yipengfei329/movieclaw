"use client";

import { useEffect, useState } from "react";

import { getHealth, type HealthResponse } from "@/lib/api/health";
import { HttpError } from "@/lib/http";

type LoadState =
  | { status: "loading"; data: null; error: null }
  | { status: "success"; data: HealthResponse; error: null }
  | { status: "error"; data: null; error: string };

function toErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    return `${error.message} (HTTP ${error.status})`;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return "Unknown error";
}

export default function HealthPage() {
  const [state, setState] = useState<LoadState>({
    status: "loading",
    data: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();

    async function loadHealth() {
      setState({
        status: "loading",
        data: null,
        error: null,
      });

      try {
        const data = await getHealth({ signal: controller.signal });
        setState({
          status: "success",
          data,
          error: null,
        });
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }

        setState({
          status: "error",
          data: null,
          error: toErrorMessage(error),
        });
      }
    }

    void loadHealth();

    return () => {
      controller.abort();
    };
  }, []);

  return (
    <div className="grid gap-6 lg:grid-cols-[0.78fr_1.22fr]">
      <section className="rounded-[32px] border border-[var(--line)] bg-[#111111] p-8 text-white shadow-[var(--shadow)]">
        <p className="text-xs font-semibold tracking-[0.24em] text-[#ffb89e] uppercase">
          Live contract check
        </p>
        <h1 className="mt-5 text-3xl font-semibold">Backend health status</h1>
        <p className="mt-4 max-w-xl text-sm leading-7 text-white/72">
          This page validates the base frontend contract: the web console talks to FastAPI through
          the shared client layer instead of calling ad hoc endpoints from the UI.
        </p>
        <div className="mt-8 rounded-[24px] border border-white/10 bg-white/6 p-5">
          <p className="text-sm text-white/60">State</p>
          <p className="mt-2 text-2xl font-semibold">
            {state.status === "loading" && "Loading"}
            {state.status === "success" && "Healthy"}
            {state.status === "error" && "Unavailable"}
          </p>
        </div>
      </section>

      <section className="rounded-[32px] border border-[var(--line)] bg-[var(--panel-strong)] p-8 shadow-[var(--shadow)]">
        {state.status === "loading" ? (
          <div className="space-y-4">
            <div className="h-4 w-24 animate-pulse rounded-full bg-black/8" />
            <div className="h-16 animate-pulse rounded-[24px] bg-black/8" />
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="h-24 animate-pulse rounded-[24px] bg-black/8" />
              <div className="h-24 animate-pulse rounded-[24px] bg-black/8" />
              <div className="h-24 animate-pulse rounded-[24px] bg-black/8" />
            </div>
          </div>
        ) : null}

        {state.status === "error" ? (
          <div className="rounded-[24px] border border-[color:rgba(153,27,27,0.14)] bg-[color:rgba(153,27,27,0.06)] p-6">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-[var(--danger)]">
              Request failed
            </p>
            <p className="mt-3 text-sm leading-7 text-[var(--foreground)]">{state.error}</p>
            <p className="mt-4 text-sm text-[var(--muted)]">
              Check whether FastAPI is running on port `8000`, or override the frontend env if the
              API lives elsewhere.
            </p>
          </div>
        ) : null}

        {state.status === "success" ? (
          <div className="space-y-5">
            <div className="flex items-center gap-3">
              <span className="inline-flex rounded-full bg-[color:rgba(22,101,52,0.1)] px-3 py-1 text-sm font-semibold text-[var(--success)]">
                {state.data.status}
              </span>
              <span className="text-sm text-[var(--muted)]">
                Backend contract from `/api/v1/health`
              </span>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <article className="rounded-[24px] border border-[var(--line)] bg-white/80 p-5">
                <p className="text-sm text-[var(--muted)]">Service</p>
                <p className="mt-2 text-xl font-semibold">{state.data.service}</p>
              </article>
              <article className="rounded-[24px] border border-[var(--line)] bg-white/80 p-5">
                <p className="text-sm text-[var(--muted)]">Environment</p>
                <p className="mt-2 text-xl font-semibold">{state.data.environment}</p>
              </article>
              <article className="rounded-[24px] border border-[var(--line)] bg-white/80 p-5">
                <p className="text-sm text-[var(--muted)]">Protocol</p>
                <p className="mt-2 text-xl font-semibold">HTTP JSON</p>
              </article>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
