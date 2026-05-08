import type { Metadata } from "next";
import Link from "next/link";

import { publicEnv } from "@/lib/env";

import "./globals.css";

export const metadata: Metadata = {
  title: publicEnv.appName,
  description: "Movieclaw Web console scaffold for future multi-client expansion.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-6 py-6 sm:px-8 lg:px-10">
          <header className="mb-8 rounded-[28px] border border-[var(--line)] bg-[var(--panel)] px-5 py-4 shadow-[var(--shadow)] backdrop-blur md:px-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div>
                <Link href="/" className="text-lg font-semibold tracking-[0.18em] uppercase">
                  MOVIECLAW
                </Link>
                <p className="mt-1 text-sm text-[var(--muted)]">
                  Web console scaffold for a multi-client product surface.
                </p>
              </div>
              <nav className="flex flex-wrap items-center gap-3 text-sm">
                <Link
                  href="/"
                  className="rounded-full border border-[var(--line)] px-4 py-2 transition hover:border-[var(--accent)] hover:bg-white/60"
                >
                  Overview
                </Link>
                <Link
                  href="/health"
                  className="rounded-full border border-[var(--line)] px-4 py-2 transition hover:border-[var(--accent)] hover:bg-white/60"
                >
                  Health Check
                </Link>
              </nav>
            </div>
          </header>
          <main className="flex-1">{children}</main>
          <footer className="mt-8 flex flex-col gap-2 border-t border-black/8 px-1 py-4 text-sm text-[var(--muted)] sm:flex-row sm:items-center sm:justify-between">
            <span>{publicEnv.appName}</span>
            <span>FastAPI backend boundary preserved for future desktop and mobile clients.</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
