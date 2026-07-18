"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PhotoIcon,
  PlayIcon,
  PlusIcon,
  StarIcon,
} from "@/components/icons";
import { ImageLightbox, type LightboxAction } from "@/components/image-lightbox";
import { MediaRow } from "@/components/media-row";
import { SubscribeDialog, type SubscribeTarget } from "@/components/subscribe-dialog";
import {
  fetchDoubanMediaDetail,
  fetchMediaDetail,
  type MediaDetailData,
  type MediaImage,
} from "@/lib/api/discover";
import { listSubscriptions, type Subscription } from "@/lib/api/subscriptions";
import { useBackdrop } from "@/lib/backdrop";
import { getMediaSeed, useMediaDetail } from "@/lib/media-detail";
import type { MediaSource, MediaType } from "@/lib/media-types";
import {
  subscriptionProgressNote,
  subscriptionStatusMeta,
} from "@/lib/subscription-ui";

/**
 * 影片详情页：点击任意海报后，主内容区整体切换为该影片的详情。
 *
 * 页面纵向结构（Apple TV / 豆瓣式）：
 *   1. Hero 大剧照 —— 有 backdropUrl 用宽幅剧照，没有则用海报重度模糊铺底
 *      （氛围色永远可用，不依赖每部影片都配横图）；左上角浮玻璃返回钮。
 *   2. 头部信息区 —— 海报上浮压住 Hero 底边，右侧标题 / 元信息 / 操作按钮，
 *      已订阅的影片额外显示订阅状态与追更进度。
 *   3. 剧情简介 + 词条信息玻璃卡（导演 / 主演 / 上映 / 地区 / 语言）。
 *   4. 剧照与海报 —— Apple TV+ 式横滚图片条，胶囊标签切换类型，点图开灯箱。
 *   5. 相似推荐 —— TMDB 推荐的相似作品，复用 MediaRow，点击可继续跳详情。
 *
 * 数据分两段呈现：点卡片时已有的列表字段（标题/海报/简介）立即渲染，
 * 词条信息与相似推荐从 /discover/{type}/{id} 异步补齐（回填片长/季数）。
 */
export function MediaDetailView({
  type,
  id,
  source = "tmdb",
}: {
  type?: MediaType;
  id: string;
  source?: MediaSource;
}) {
  const { close } = useMediaDetail();
  const [detail, setDetail] = useState<MediaDetailData | null>(null);
  // 详情拉取失败状态：仅在无 seed（硬刷新/分享直达）时才需要整页兜底
  const [loadFailed, setLoadFailed] = useState(false);
  // 该条目的订阅（从订阅列表按外部 ID 匹配；订阅/取消后重新拉取）
  const [sub, setSub] = useState<Subscription | undefined>(undefined);
  // 订阅弹层的打开参数；null = 关闭
  const [subscribeTarget, setSubscribeTarget] = useState<SubscribeTarget | null>(null);

  const reloadSubscription = useCallback(() => {
    listSubscriptions()
      .then((rows) =>
        setSub(
          rows.find((s) =>
            source === "douban"
              ? s.media.douban_id === id
              : s.media.kind === (type ?? "movie") && String(s.media.tmdb_id) === id,
          ),
        ),
      )
      .catch(() => setSub(undefined));
  }, [id, source, type]);

  useEffect(() => {
    setSub(undefined);
    reloadSubscription();
  }, [reloadSubscription]);
  // 站内点卡片跳转时预存的列表字段（标题/海报/简介），用于首屏零白屏；
  // 硬刷新 / 分享链接直达时为空，此时全靠 /discover/{type}/{id} 拉取。
  const listItem = getMediaSeed(source, id);

  useEffect(() => {
    setDetail(null);
    setLoadFailed(false);
    let cancelled = false;
    const request =
      source === "douban"
        ? fetchDoubanMediaDetail(id)
        : fetchMediaDetail(type ?? "movie", id);
    request
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch(() => {
        // 有 seed 时详情拉取失败不打断页面：列表字段仍可完整展示；
        // 无 seed（直达）时则没有任何可渲染内容，标记失败以显示兜底。
        if (!cancelled) setLoadFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, source, type]);

  // 详情接口回填过 extent（片长/季数）等字段，未返回前先用列表字段渲染
  const item = detail?.item ?? listItem;

  // 无 seed 且详情尚未到达：直达链接的加载态（或失败兜底）。
  if (!item) {
    return <DetailFallback failed={loadFailed} onBack={close} />;
  }

  const info = detail?.info;
  const related = detail?.related ?? [];

  const isMovie = item.type === "movie";

  /** 打开订阅弹层：TMDB 入口直接带 tmdb_id；豆瓣入口交给后端收敛。 */
  const openSubscribe = () =>
    setSubscribeTarget({
      kind: item.type,
      source,
      tmdbId: source === "tmdb" ? Number(id) : undefined,
      doubanId: source === "douban" ? id : undefined,
      title: item.title,
      year: item.year || undefined,
    });

  return (
    <div className="scroll-thin h-full overflow-y-auto pb-12">
      {/* —— 1. Hero 大剧照 —— */}
      <div className="px-6 pt-5">
        <div className="relative h-[42vh] min-h-[280px] overflow-hidden rounded-2xl shadow-[0_24px_70px_-18px_rgba(0,0,0,0.62)] ring-1 ring-white/10">
          {item.backdropUrl ? (
            <img
              src={item.backdropUrl}
              alt={`${item.title} 剧照`}
              className="absolute inset-0 size-full object-cover object-top"
            />
          ) : (
            // 无横幅剧照时的兜底：海报放大 + 重度模糊，产出该片专属的氛围底色
            <img
              src={item.posterUrl}
              alt=""
              aria-hidden="true"
              className="absolute inset-0 size-full scale-125 object-cover blur-3xl brightness-[0.72] saturate-[1.25]"
            />
          )}
          {/* 双层渐变：左侧压暗、底部渐隐融入页面，保证叠加文字可读 */}
          <div className="absolute inset-0 bg-gradient-to-r from-[rgba(7,9,14,0.72)] via-[rgba(7,9,14,0.25)] to-transparent" />
          <div className="absolute inset-x-0 bottom-0 h-2/3 bg-gradient-to-t from-[rgba(7,9,14,0.88)] via-[rgba(7,9,14,0.35)] to-transparent" />

          <button
            type="button"
            aria-label="返回"
            onClick={close}
            className="surface-raised absolute left-4 top-4 z-10 flex size-9 items-center justify-center !rounded-full text-[var(--text)] transition-transform duration-200 hover:scale-110"
          >
            <ArrowLeftIcon className="size-4" />
          </button>
        </div>
      </div>

      {/* —— 2. 头部信息区：海报上浮压住 Hero 底边 —— */}
      <div className="relative z-10 -mt-44 flex items-end gap-7 px-12">
        <div className="w-[186px] shrink-0 overflow-hidden rounded-xl bg-[#141824] shadow-[0_26px_60px_rgba(0,0,0,0.6)] ring-1 ring-white/15">
          <img
            src={item.posterUrl}
            alt={`${item.title} 海报`}
            className="aspect-[2/3] w-full object-cover"
          />
        </div>

        <div className="min-w-0 flex-1 pb-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[var(--accent-2)]">
            {source === "douban" ? "豆瓣" : "TMDB"} · {isMovie ? "电影" : "剧集"}
            {item.genres.length > 0 ? ` · ${item.genres.join(" / ")}` : ""}
          </p>
          <h1 className="text-on-image mt-2 text-[38px] font-bold leading-[1.1] tracking-[-0.02em] text-white">
            {item.title}
          </h1>
          <p className="text-on-image mt-1.5 truncate text-[14px] text-white/55">
            {item.originalTitle}
          </p>

          {/* 元信息行：评分 / 年份 / 规模 / 质量徽章 */}
          <div className="tnum mt-3.5 flex flex-wrap items-center gap-x-3.5 gap-y-2 text-[13px] text-white/80">
            <span className="flex items-center gap-1.5">
              <StarIcon className="size-4 text-[#f5c451]" />
              <span className="text-[16px] font-bold text-white">{item.rating.toFixed(1)}</span>
            </span>
            <span>{item.year}</span>
            {item.extent && <span>{item.extent}</span>}
            {item.badges.length > 0 && (
              <span className="flex gap-1.5">
                {item.badges.map((b) => (
                  <span
                    key={b}
                    className="rounded border border-white/25 px-1.5 py-px text-[10px] font-semibold tracking-wide text-white/85"
                  >
                    {b}
                  </span>
                ))}
              </span>
            )}
          </div>

          {/* 操作区：已订阅的影片主按钮变为状态展示（点击进入管理弹层可取消订阅） */}
          <div className="mt-5 flex flex-wrap items-center gap-3">
            {sub ? (
              <button
                type="button"
                onClick={openSubscribe}
                className="btn-glass flex h-10 items-center gap-2 bg-white/10 px-5 text-[13px] font-medium backdrop-blur-md transition hover:bg-white/15"
              >
                <CheckIcon
                  className="size-4"
                  style={{ color: subscriptionStatusMeta[sub.status].color }}
                />
                已订阅 · {subscriptionStatusMeta[sub.status].label}
              </button>
            ) : (
              <button
                type="button"
                onClick={openSubscribe}
                className="btn-accent flex h-10 items-center gap-2 rounded-full px-5 text-[13px] font-semibold"
              >
                <PlayIcon className="size-4" />
                订阅追踪
              </button>
            )}
            <button
              type="button"
              className="btn-glass h-10 bg-white/10 px-5 text-[13px] font-medium backdrop-blur-md"
            >
              <PlusIcon className="size-4" />
              加入想看
            </button>
            {source === "douban" && info?.sourceUrl && (
              <a
                href={info.sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="btn-glass flex h-10 items-center bg-white/10 px-5 text-[13px] font-medium backdrop-blur-md transition hover:bg-white/15"
              >
                去豆瓣查看
              </a>
            )}
            {sub && (
              <span className="text-on-image flex items-center gap-1.5 text-[12.5px] text-[var(--text-muted)]">
                <span
                  className="size-1.5 rounded-full"
                  style={{ backgroundColor: subscriptionStatusMeta[sub.status].color }}
                />
                {subscriptionProgressNote(sub)}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 订阅弹层：prepare → 季选择/追新/规则组 → 创建；已订阅时为管理态 */}
      <SubscribeDialog
        target={subscribeTarget}
        onClose={() => setSubscribeTarget(null)}
        onChanged={reloadSubscription}
      />

      {/* —— 3. 剧情简介 + 词条信息 —— */}
      <div className="mt-9 space-y-8 px-12">
        <section>
          <h2 className="text-on-image mb-3 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text)]">
            剧情简介
          </h2>
          <p className="text-on-image max-w-3xl text-[14px] leading-7 text-white/78">
            {item.overview}
          </p>
        </section>

        {info && (
          <section>
            <h2 className="text-on-image mb-3 text-[15px] font-semibold tracking-[-0.01em] text-[var(--text)]">
              词条信息
            </h2>
            <div className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-6 backdrop-blur-xl">
              <dl className="grid gap-x-10 gap-y-5 sm:grid-cols-2 xl:grid-cols-3">
                {info.directors.length > 0 && (
                  <Fact label={isMovie ? "导演" : "主创"} value={info.directors.join(" / ")} />
                )}
                {info.cast.length > 0 && <Fact label="主演" value={info.cast.join(" / ")} />}
                {info.released && (
                  <Fact label={isMovie ? "上映日期" : "首播日期"} value={info.released} />
                )}
                {info.network && <Fact label="播出平台" value={info.network} />}
                {info.country && <Fact label="制片地区" value={info.country} />}
                {info.language && <Fact label="语言" value={info.language} />}
                {item.extent && <Fact label={isMovie ? "片长" : "规模"} value={item.extent} />}
                {info.aliases.length > 0 && (
                  <Fact label="别名" value={info.aliases.join(" / ")} />
                )}
                {item.badges.length > 0 && (
                  <Fact label="资源规格" value={item.badges.join(" · ")} />
                )}
              </dl>
            </div>
          </section>
        )}
      </div>

      {/* —— 4. 剧照与海报 —— */}
      {detail && (detail.backdrops.length > 0 || detail.posters.length > 0) && (
        <div className="mt-9 px-12">
          <PhotoWall
            title={item.title}
            backdrops={detail.backdrops}
            posters={detail.posters}
          />
        </div>
      )}

      {/* —— 5. 相似推荐 —— */}
      {related.length > 0 && (
        <div className="mt-9">
          <MediaRow row={{ id: `related-${item.id}`, title: "相似推荐", items: related }} />
        </div>
      )}
    </div>
  );
}

/**
 * 剧照与海报（Apple TV+ 式图片横滚条 + IMDb 式类型切换）：
 *   - 「剧照 / 海报」胶囊标签切换（无图的类型不渲染标签）；
 *   - 剧照 16:9、海报 2:3，等高排成一行横滚，隐藏滚动条，
 *     hover 时两侧浮现翻页钮（与发现页海报行同一套交互语言）；
 *   - 点任意缩略图 → 全屏灯箱看原图（复用 ImageLightbox：←→ 切换 + 缩略图条）。
 */
function PhotoWall({
  title,
  backdrops,
  posters,
}: {
  title: string;
  backdrops: MediaImage[];
  posters: MediaImage[];
}) {
  const tabs = [
    { id: "backdrops" as const, label: "剧照", images: backdrops },
    { id: "posters" as const, label: "海报", images: posters },
  ].filter((t) => t.images.length > 0);
  const [activeId, setActiveId] = useState(tabs[0].id);
  // 灯箱：记录打开时的图片下标；null = 关闭
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  const active = tabs.find((t) => t.id === activeId) ?? tabs[0];
  const { uploadBackdrop } = useBackdrop();

  // 「设为背景」（仅剧照：16:9 宽幅才适合铺满视口；海报是 2:3 竖图不提供）。
  // 完全复用外观设置的上传链路：拉取 TMDB 原图（图床允许跨域）→ 压缩成 2560px
  // JPEG → POST /appearance/backdrops 入库并生效 → 全站背景与外观设置图库同步更新。
  const setAsBackdrop: LightboxAction | undefined =
    active.id === "backdrops"
      ? {
          label: "设为背景",
          busyLabel: "正在下载并设置…",
          doneLabel: "已设为背景",
          icon: <PhotoIcon className="size-3.5" />,
          run: async (i: number) => {
            const image = active.images[i];
            let blob: Blob;
            try {
              // cache:no-store 绕过 HTTP 缓存：灯箱 <img> 已用 no-cors 模式加载过
              // 这张图，缓存里是不带 CORS 头的响应（CDN Vary: Origin），直接 fetch
              // 会命中污染缓存被判跨域失败——必须强制重新请求
              const resp = await fetch(image.fullUrl, { cache: "no-store" });
              if (!resp.ok) throw new Error();
              blob = await resp.blob();
            } catch {
              throw new Error("下载剧照原图失败，请检查网络后重试");
            }
            await uploadBackdrop(
              new File([blob], "backdrop.jpg", { type: blob.type || "image/jpeg" }),
            );
          },
        }
      : undefined;

  const scrollerRef = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  /** 与 MediaRow 同款的边缘检测：到达两端时隐藏对应方向的翻页钮 */
  const updateEdges = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setCanLeft(el.scrollLeft > 1);
    setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    updateEdges();
    window.addEventListener("resize", updateEdges);
    return () => window.removeEventListener("resize", updateEdges);
  }, [updateEdges, activeId]);

  const page = (dir: -1 | 1) => {
    const el = scrollerRef.current;
    el?.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: "smooth" });
  };

  const switchTab = (id: typeof activeId) => {
    setActiveId(id);
    // 切换类型回到行首，避免带着上一类的滚动位置看新列表
    scrollerRef.current?.scrollTo({ left: 0 });
  };

  return (
    <section className="group/photos">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-on-image text-[15px] font-semibold tracking-[-0.01em] text-[var(--text)]">
          剧照与海报
        </h2>
        {tabs.length > 1 && (
          <div className="flex gap-1.5">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                aria-pressed={tab.id === activeId}
                onClick={() => switchTab(tab.id)}
                className={`tnum rounded-full px-3 py-1 text-[12px] font-medium transition-colors ${
                  tab.id === activeId
                    ? "bg-white/[0.14] text-white"
                    : "text-[var(--text-muted)] hover:bg-white/[0.07] hover:text-[var(--text)]"
                }`}
              >
                {tab.label} {tab.images.length}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="relative">
        <div
          ref={scrollerRef}
          onScroll={updateEdges}
          className="scroll-none -mx-1 flex gap-3 overflow-x-auto px-1 pb-1 pt-1"
        >
          {active.images.map((img, i) => (
            <button
              key={img.previewUrl}
              type="button"
              aria-label={`查看${active.label}第 ${i + 1} 张`}
              onClick={() => setLightboxIndex(i)}
              className={`shrink-0 overflow-hidden rounded-xl bg-[#141824] ring-1 ring-white/[0.08] transition-all duration-300 ease-out hover:-translate-y-1 hover:shadow-[0_16px_40px_rgba(0,0,0,0.55)] hover:ring-white/30 ${
                active.id === "backdrops" ? "aspect-video h-[148px]" : "aspect-[2/3] h-[148px]"
              }`}
            >
              <img
                src={img.previewUrl}
                alt={`${title} ${active.label}`}
                loading="lazy"
                className="size-full object-cover transition-transform duration-500 ease-out hover:scale-[1.05]"
              />
            </button>
          ))}
        </div>

        <PhotoArrow dir={-1} visible={canLeft} onClick={() => page(-1)} />
        <PhotoArrow dir={1} visible={canRight} onClick={() => page(1)} />
      </div>

      {lightboxIndex !== null && (
        <ImageLightbox
          images={active.images.map((img) => img.fullUrl)}
          initialIndex={lightboxIndex}
          title={`${title} · ${active.label}`}
          action={setAsBackdrop}
          thumbAspect={active.id === "backdrops" ? "landscape" : "portrait"}
          onClose={() => setLightboxIndex(null)}
        />
      )}
    </section>
  );
}

/** 图片条的翻页钮：!absolute 同 MediaRow —— surface-raised 自带 relative 会盖掉 absolute */
function PhotoArrow({
  dir,
  visible,
  onClick,
}: {
  dir: -1 | 1;
  visible: boolean;
  onClick: () => void;
}) {
  const Icon = dir === -1 ? ChevronLeftIcon : ChevronRightIcon;
  return (
    <button
      type="button"
      aria-label={dir === -1 ? "向左滚动" : "向右滚动"}
      onClick={onClick}
      className={`surface-raised !absolute top-1/2 z-10 flex size-9 -translate-y-1/2 items-center justify-center !rounded-full text-[var(--text)] transition-all duration-200 hover:scale-110 ${
        dir === -1 ? "left-2" : "right-2"
      } ${
        visible
          ? "pointer-events-auto opacity-0 group-hover/photos:opacity-100"
          : "pointer-events-none opacity-0"
      }`}
    >
      <Icon className="size-4" />
    </button>
  );
}

/**
 * 直达详情页（硬刷新 / 分享链接）时的整页兜底：
 * 站内跳转有 seed 可秒开，直达则先转圈等接口；接口失败给出错误 + 返回入口。
 */
function DetailFallback({ failed, onBack }: { failed: boolean; onBack: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      {failed ? (
        <>
          <p className="text-[15px] font-semibold text-[var(--text)]">未能加载该影片详情</p>
          <p className="max-w-sm text-[13px] leading-6 text-[var(--text-muted)]">
            资源可能已下线，或网络暂时不可达。请返回后重试。
          </p>
          <button
            type="button"
            onClick={onBack}
            className="btn-glass px-4 py-2 text-[13px] font-medium text-[var(--text)]"
          >
            <ArrowLeftIcon className="size-4" />
            返回
          </button>
        </>
      ) : (
        <div className="flex items-center gap-2.5 text-[13px] text-[var(--text-muted)]">
          <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          正在加载详情…
        </div>
      )}
    </div>
  );
}

/** 词条信息的单个条目：弱化的标签 + 常规值 */
function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] text-[var(--text-faint)]">{label}</dt>
      <dd className="mt-1 text-[13.5px] leading-6 text-[var(--text)]">{value}</dd>
    </div>
  );
}
