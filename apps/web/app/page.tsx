import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Overview",
};

const pillars = [
  {
    title: "Web As A Client",
    description:
      "The console consumes the Python API boundary instead of becoming a second backend.",
  },
  {
    title: "Ready For More Surfaces",
    description:
      "This repository can grow into desktop and mobile apps without rewriting the web foundation.",
  },
  {
    title: "Tailwind Design Tokens",
    description:
      "Global tokens are in place now, so adding a full component system later will not require a reset.",
  },
];

const nextSteps = [
  "Authentication and permission model",
  "Tracker configuration and credential workflows",
  "Task dashboards, jobs, and history views",
];

export default function HomePage() {
  return (
    <div className="space-y-6">
      <section className="grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
        <div className="rounded-[32px] border border-[var(--line)] bg-[var(--panel-strong)] p-8 shadow-[var(--shadow)]">
          <div className="inline-flex rounded-full border border-[var(--line)] bg-white/80 px-3 py-1 text-xs font-semibold tracking-[0.24em] text-[var(--accent-strong)] uppercase">
            Movieclaw Console
          </div>
          <h1 className="mt-6 max-w-3xl text-4xl font-semibold leading-tight sm:text-5xl">
            A clean web surface for a Python-first crawler and automation platform.
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-7 text-[var(--muted)] sm:text-lg">
            The backend stays in FastAPI. The web layer stays independent. This is the minimal
            shell that lets you grow toward a full control console without mixing product logic
            into framework glue.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/health"
              className="rounded-full bg-[var(--foreground)] px-5 py-3 text-sm font-semibold text-white transition hover:bg-black"
            >
              Open health check
            </Link>
            <span className="rounded-full border border-[var(--line)] px-4 py-3 text-sm text-[var(--muted)]">
              App Router + TypeScript + Tailwind CSS
            </span>
          </div>
        </div>

        <aside className="rounded-[32px] border border-[var(--line)] bg-[#111111] p-8 text-white shadow-[var(--shadow)]">
          <p className="text-xs font-semibold tracking-[0.24em] text-[#ffb89e] uppercase">
            Current baseline
          </p>
          <div className="mt-5 space-y-4">
            <div>
              <p className="text-3xl font-semibold">1 repo</p>
              <p className="mt-1 text-sm text-white/70">
                Python backend and web frontend remain isolated but packaged together later.
              </p>
            </div>
            <div>
              <p className="text-3xl font-semibold">3 targets</p>
              <p className="mt-1 text-sm text-white/70">
                Web now, desktop and mobile next, all sharing the same API boundary.
              </p>
            </div>
            <div>
              <p className="text-3xl font-semibold">0 lock-in</p>
              <p className="mt-1 text-sm text-white/70">
                No SSR-only business path, no forced component library, no fake full-stack setup.
              </p>
            </div>
          </div>
        </aside>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {pillars.map((pillar) => (
          <article
            key={pillar.title}
            className="rounded-[28px] border border-[var(--line)] bg-[var(--panel)] p-6 shadow-[var(--shadow)]"
          >
            <h2 className="text-lg font-semibold">{pillar.title}</h2>
            <p className="mt-3 text-sm leading-6 text-[var(--muted)]">{pillar.description}</p>
          </article>
        ))}
      </section>

      <section className="grid gap-6 lg:grid-cols-[0.95fr_1.05fr]">
        <article className="rounded-[28px] border border-[var(--line)] bg-[var(--panel)] p-6 shadow-[var(--shadow)]">
          <p className="text-sm font-semibold tracking-[0.18em] text-[var(--accent-strong)] uppercase">
            Included now
          </p>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-[var(--muted)]">
            <li>Independent workspace app under `apps/web`.</li>
            <li>Shared HTTP client layer under `lib/http.ts`.</li>
            <li>Health page wired to the FastAPI backend contract.</li>
            <li>Development proxy for local `3000 to 8000` API traffic.</li>
          </ul>
        </article>
        <article className="rounded-[28px] border border-[var(--line)] bg-[var(--panel)] p-6 shadow-[var(--shadow)]">
          <p className="text-sm font-semibold tracking-[0.18em] text-[var(--accent-strong)] uppercase">
            Next surface area
          </p>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-[var(--muted)]">
            {nextSteps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ul>
        </article>
      </section>
    </div>
  );
}
