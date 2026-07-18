"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { ArrowLeftIcon, SearchIcon } from "@/components/icons";
import { PosterCard } from "@/components/poster-card";
import { fetchDiscoverPage } from "@/lib/api/discover";
import type { MediaItem } from "@/lib/media-types";

const PAGE_SIZE = 50;
const TOP250_ROW_ID = "douban-movie_top250";

/**
 * 豆瓣 Top 250 完整榜单：用纵向网格承载大量条目，避免 250 张海报挤在一条横滚行。
 * 数据仍复用发现接口和后端缓存；前端只分批挂载图片节点，控制首屏开销。
 */
export function Top250View() {
  const [items, setItems] = useState<MediaItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [selectedGenres, setSelectedGenres] = useState<string[]>([]);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [error, setError] = useState<string | null>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDiscoverPage("movie", "douban")
      .then((page) => {
        const row = page.rows.find((candidate) => candidate.id === TOP250_ROW_ID);
        if (!row) throw new Error("豆瓣 Top 250 榜单暂不可用");
        if (!cancelled) setItems(row.items);
      })
      .catch((reason: Error) => {
        if (!cancelled) setError(reason.message || "榜单加载失败，请稍后重试");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const genres = useMemo(() => {
    if (!items) return [];
    const counts = new Map<string, number>();
    for (const item of items) {
      for (const name of item.genres) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts].sort((a, b) => b[1] - a[1]);
  }, [items]);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    if (!items) return [];
    return items.filter((item) => {
      // 同一筛选维度采用「或」逻辑：选择科幻 + 动画即显示任一类型命中的影片。
      const matchesGenre =
        selectedGenres.length === 0 ||
        selectedGenres.some((selected) => item.genres.includes(selected));
      const matchesKeyword =
        !keyword ||
        item.title.toLocaleLowerCase().includes(keyword) ||
        item.originalTitle.toLocaleLowerCase().includes(keyword);
      return matchesGenre && matchesKeyword;
    });
  }, [items, query, selectedGenres]);

  const hasMore = visibleCount < filtered.length;

  // 接近列表底部时自动追加一批；依赖筛选后的总数，切换类型后按新结果重新观察。
  useEffect(() => {
    const target = loadMoreRef.current;
    if (!target || !hasMore) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisibleCount((count) => Math.min(count + PAGE_SIZE, filtered.length));
        }
      },
      { rootMargin: "500px 0px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [filtered.length, hasMore]);

  const updateQuery = (value: string) => {
    setQuery(value);
    setVisibleCount(PAGE_SIZE);
  };

  const toggleGenre = (value: string) => {
    setSelectedGenres((current) =>
      current.includes(value)
        ? current.filter((selected) => selected !== value)
        : [...current, value],
    );
    setVisibleCount(PAGE_SIZE);
  };

  const clearGenres = () => {
    setSelectedGenres([]);
    setVisibleCount(PAGE_SIZE);
  };

  return (
    <div className="scroll-thin flex-1 overflow-y-auto px-6 pb-12 pt-5">
      <header className="mx-auto max-w-[1500px]">
        <Link
          href="/discover/movie?source=douban"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--text-muted)] transition hover:text-[var(--text)]"
        >
          <ArrowLeftIcon className="size-4" />
          返回发现电影
        </Link>
        <div className="mt-5 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--accent-2)]">
              DOUBAN RANKING
            </p>
            <h1 className="mt-1 text-3xl font-bold tracking-[-0.03em] text-[var(--text)]">
              豆瓣电影 Top 250
            </h1>
            <p className="mt-2 text-sm text-[var(--text-muted)]">
              {items ? `完整收录 ${items.length} 部影片` : "正在读取完整榜单…"}
            </p>
          </div>
          <label className="flex h-10 w-full items-center gap-2 rounded-full border border-white/10 bg-black/25 px-4 text-[var(--text-muted)] backdrop-blur-sm sm:w-72">
            <SearchIcon className="size-4 shrink-0" />
            <input
              value={query}
              onChange={(event) => updateQuery(event.target.value)}
              placeholder="搜索片名"
              aria-label="搜索 Top 250 片名"
              className="min-w-0 flex-1 bg-transparent text-sm text-[var(--text)] outline-none placeholder:text-[var(--text-muted)]"
            />
          </label>
        </div>
        {genres.length > 0 && (
          <div className="mt-6 flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-semibold text-[var(--text-muted)]">类型</span>
            <button
              type="button"
              aria-pressed={selectedGenres.length === 0}
              onClick={clearGenres}
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                selectedGenres.length === 0
                  ? "border-white/20 bg-white/15 text-white"
                  : "border-white/[0.07] bg-black/20 text-[var(--text-muted)] hover:border-white/15 hover:text-white"
              }`}
            >
              全部
            </button>
            {genres.map(([name, count]) => (
              <button
                key={name}
                type="button"
                aria-pressed={selectedGenres.includes(name)}
                onClick={() => toggleGenre(name)}
                className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                  selectedGenres.includes(name)
                    ? "border-white/20 bg-white/15 text-white"
                    : "border-white/[0.07] bg-black/20 text-[var(--text-muted)] hover:border-white/15 hover:text-white"
                }`}
              >
                {name}
                <span className="tnum ml-1 text-[10px] opacity-55">{count}</span>
              </button>
            ))}
            {selectedGenres.length > 0 && (
              <button
                type="button"
                onClick={clearGenres}
                className="ml-1 rounded-full px-2 py-1.5 text-xs font-semibold text-[var(--accent-2)] transition hover:text-white"
              >
                清除筛选（{selectedGenres.length}）
              </button>
            )}
          </div>
        )}
      </header>

      {error && (
        <div className="mx-auto mt-16 max-w-md rounded-2xl border border-white/10 bg-black/25 p-8 text-center text-sm text-[var(--text-muted)]">
          {error}
        </div>
      )}

      {!items && !error && <Top250Skeleton />}

      {items && (
        <main className="mx-auto mt-8 max-w-[1500px]">
          {filtered.length === 0 ? (
            <div className="py-20 text-center text-sm text-[var(--text-muted)]">
              没有找到匹配的影片
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8">
              {filtered.slice(0, visibleCount).map((item) => {
                const rank = items.indexOf(item) + 1;
                return (
                  <div key={item.id} className="relative min-w-0">
                    <RankBadge rank={rank} />
                    <PosterCard item={item} />
                  </div>
                );
              })}
            </div>
          )}

          <div
            ref={loadMoreRef}
            className="mt-8 flex h-10 items-center justify-center text-xs text-[var(--text-muted)]"
            aria-live="polite"
          >
            {hasMore
              ? `继续滚动加载（已显示 ${visibleCount} / ${filtered.length}）`
              : filtered.length > 0
                ? `已显示全部 ${filtered.length} 部影片`
                : ""}
          </div>
        </main>
      )}
    </div>
  );
}

function RankBadge({ rank }: { rank: number }) {
  const tone =
    rank === 1
      ? "bg-[#d8ad50] text-[#211704]"
      : rank === 2
        ? "bg-[#b9c1cc] text-[#171a20]"
        : rank === 3
          ? "bg-[#b9794c] text-[#211108]"
          : "bg-black/70 text-white";
  return (
    <span
      className={`tnum absolute -left-1.5 -top-1.5 z-10 min-w-8 rounded-lg px-2 py-1 text-center text-xs font-black shadow-lg ring-1 ring-white/15 ${tone}`}
    >
      {rank}
    </span>
  );
}

function Top250Skeleton() {
  return (
    <div className="mx-auto mt-8 grid max-w-[1500px] grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8">
      {Array.from({ length: 24 }, (_, index) => (
        <div
          key={index}
          className="aspect-[2/3] animate-pulse rounded-2xl bg-white/[0.05] ring-1 ring-white/10"
        />
      ))}
    </div>
  );
}
