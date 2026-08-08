"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import type { Route } from "next";

import {
  ArrowLeftIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FolderIcon,
  PhotoIcon,
  BellIcon,
  SearchIcon,
  StarIcon,
} from "@/components/icons";
import { CastRow } from "@/components/cast-row";
import { PageNav } from "@/components/page-nav";
import { ImageLightbox, type LightboxAction } from "@/components/image-lightbox";
import { MediaRow } from "@/components/media-row";
import { PosterImage } from "@/components/poster-image";
import { SubscribeDialog, type SubscribeTarget } from "@/components/subscribe-dialog";
import {
  fetchDoubanMediaDetail,
  fetchMediaDetail,
  type MediaDetailData,
  type MediaImage,
} from "@/lib/api/discover";
import { useSubscribeEntry } from "@/components/subscribe-entry";
import { useBackdrop } from "@/lib/backdrop";
import { buildDiscoveryReturnPath } from "@/lib/discovery-return-path";
import { useDoubanAppHref } from "@/lib/douban-app-link";
import { getMediaOrigin, getMediaSeed, useMediaDetail } from "@/lib/media-detail";
import { usePageTitle } from "@/lib/use-page-title";
import type { MediaSource, MediaType } from "@/lib/media-types";
import {
  subscriptionProgressNote,
  subscriptionStatusMeta,
} from "@/lib/subscription-ui";

/**
 * 影片详情页：点击任意海报后，主内容区整体切换为该影片的详情。
 *
 * 骨架与媒体库条目详情页（LibraryItemDetailView）保持一致——同样是「影片详情」，
 * 用户不该因为这部片在不在库里就换一套阅读结构。两页的差别只在**信息来源**：
 * 那边是本地刮削成果与文件本体（我拥有的这份拷贝是什么），这边是 TMDB / 豆瓣
 * 的在线信息（这部片本身是什么），因此这边多出剧照墙与相似推荐，那边多出
 * 片源规格与文件区。
 *
 * 页面纵向结构：
 *   1. 沉浸背景 —— 进入本页时把全站背景临时换成该片剧照（setOverrideBackdrop），
 *      页面本身不再画 Hero 卡片：大图直出、零边界。顶栏首屏只有一颗返回键浮在
 *      剧照上。没有横幅剧照的条目退回海报（背景层自己会铺满并模糊）。
 *      豆瓣来源例外：图源只有小尺寸海报，铺满是糊图，索性保留用户配置的背景。
 *   2. 氛围留白 + 渐变内容层 —— 留一段什么都不放的高度让剧照呼吸，其下从全透明
 *      渐入页面底色，与背景之间没有接缝。
 *   3. 头部信息区 —— 海报 + 标题 / 元信息 / 外部词条链接 / 订阅操作，
 *      已订阅的影片额外显示订阅状态与追更进度。
 *   4. 剧情简介。
 *   5. 演职员 —— 带头像的横滚条（与条目详情页同一套版式），数据来自 TMDB
 *      credits / 豆瓣 actors，含角色名。
 *   6. 词条信息玻璃卡（上映 / 地区 / 语言 / 平台 / 别名）——「主演」已经由
 *      演职员条完整呈现，不在这里重复成一行文字。
 *   7. 剧照与海报 —— Apple TV+ 式横滚图片条，胶囊标签切换类型，点图开灯箱。
 *   8. 相似推荐 —— 复用 MediaRow，点击可继续跳详情。
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
  // 该条目的订阅：与海报卡片同一份全站订阅状态（subscribe-entry 收口），
  // 订阅/取消后 refresh 一次，详情页与所有卡片同步更新
  const { subscriptionOf, refresh: refreshSubscriptions } = useSubscribeEntry();
  const sub = subscriptionOf({ id, source, type: type ?? "movie" });
  // 订阅弹层的打开参数；null = 关闭
  const [subscribeTarget, setSubscribeTarget] = useState<SubscribeTarget | null>(null);
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
  usePageTitle(item?.title);

  // 沉浸背景：进入本页把全站背景临时换成该片剧照，离开即恢复用户配置的背景。
  // 与媒体库条目详情页同一条链路（见 lib/backdrop.tsx 的 overrideUrl）。
  // 没有横幅剧照时退回海报——背景层自己会铺满，竖图拉伸后本就是一层氛围色。
  //
  // 豆瓣来源不换背景：豆瓣只有小尺寸海报、没有高清横幅剧照，铺成全屏背景是一片
  // 糊图，比用户自己配置的背景差得多。宁可保持原背景，也不要为了"沉浸"降质。
  const { setOverrideBackdrop } = useBackdrop();
  const immersiveUrl =
    source === "douban" ? "" : item?.backdropUrl || item?.posterUrl || "";
  useEffect(() => {
    if (!immersiveUrl) return;
    setOverrideBackdrop(immersiveUrl);
    return () => setOverrideBackdrop(null);
  }, [immersiveUrl, setOverrideBackdrop]);

  // 豆瓣外链的移动端 App 直跳：无悬停设备把「豆瓣」外链换成官方分发地址，
  // 装了豆瓣 App 直接拉起进词条页（桌面/未命中时为 null，回落网页地址）
  const doubanAppHref = useDoubanAppHref(source === "douban" ? id : null);

  // 来路祖先（与影片标题无关）：加载/失败兜底与正常内容共用同一条回跳链路。
  // 兜底态也必须渲染 PageNav——它向外壳登记「本页自带顶栏」，否则移动端的
  // 全局顶栏（☰ + logo）会在数据到达前先显示、随后又消失，顶部闪一下；
  // 顺带让用户在转圈期间就有返回键可点。
  const ancestors = getMediaOrigin(source, id) ?? [
    (item?.type ?? type ?? "movie") === "movie"
      ? { label: "发现电影", href: "/discover/movie" }
      : { label: "发现剧集", href: "/discover/tv" },
  ];

  // 无 seed 且详情尚未到达：直达链接的加载态（或失败兜底）。
  if (!item) {
    return (
      <div className="flex h-full flex-col">
        {/* 当前页标题未知，末项留空——只为立起返回键并认领顶栏 */}
        <PageNav items={[...ancestors, { label: "" }]} />
        <DetailFallback failed={loadFailed} onBack={close} />
      </div>
    );
  }

  const info = detail?.info;
  const related = detail?.related ?? [];
  const libraryLinks = detail?.libraryLinks ?? [];
  // 发现详情路由是媒体库页可安全返回的唯一来源；不从标题或媒体库数据反推条目。
  const libraryReturnTo = buildDiscoveryReturnPath(source, type ?? item.type, id);

  const isMovie = item.type === "movie";
  // 词条信息卡里是否还剩下至少一格（导演已上提到元信息行、主演已交给演职员条）
  const hasFacts = Boolean(
    info &&
      (info.released ||
        info.network ||
        info.country ||
        info.language ||
        item.extent ||
        info.aliases.length > 0 ||
        item.badges.length > 0),
  );

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

  // 来路：站内点进来按来的列表页作返回目标；直达/刷新按影片类型兜底
  const trail = [...ancestors, { label: item.title }];

  return (
    // rounded-2xl + overflow 裁切：内容渐变到底部是近实色的深色板，方角会与
    // 全站「浮起圆角卡片」的形状语言冲突——按侧栏同规格圆角收尾。
    // max-md:rounded-none：圆角只在桌面成立（外壳 p-3.5 的留白托着卡片）；
    // 窄屏通栏满屏，圆角会直接压在屏幕边上，把吸顶顶栏裁成一块贴在屏幕顶上的
    // 圆角色块——与 library-item-detail-view 同一处理。
    <div className="scroll-thin scroll-safe h-full overflow-y-auto rounded-2xl max-md:rounded-none">
      {/* min-h-full + flex 纵向：内容不足一屏时（简介/演职员/剧照都缺的小众条目）
          内容层仍要撑满剩余高度。否则渐变板只到内容底部就断了，下面直接露出
          沉浸背景，板子底边成了一道硬边界——正是这套「无缝渐入」要避免的东西。
          PageNav 仍在滚动容器内（sticky 参照的是最近滚动祖先，中间这层普通 div
          不影响），其活动范围由这层的高度决定，覆盖整个可滚动区间。 */}
      <div className="flex min-h-full flex-col">
      {/* —— 1. 顶栏：不再有任何 Hero 卡片图层——全站背景此刻就是本片剧照
          （沉浸覆盖 + 本页豁免全局蒙版，见 app-shell 的 isHome），大图直出、
          零边界。首屏只有一颗圆形返回键浮在剧照上 —— */}
      <PageNav items={trail} />

      {/* 氛围留白：这一段什么都不放，让剧照完整呼吸。shrink-0 是必须的——
          它是 flex 子项，不加会在内容长的时候被压扁 */}
      <div className="h-[30vh] min-h-[180px] shrink-0 max-md:h-[22vh] max-md:min-h-[120px]" />

      {/* —— 2. 内容层：从全透明渐入页面底色，与背景之间没有任何接缝 —— */}
      <div className="flex-1 bg-[linear-gradient(180deg,rgba(7,9,14,0)_0,rgba(7,9,14,0.66)_130px,rgba(7,9,14,0.88)_360px,rgba(7,9,14,0.93)_100%)] pb-12">
      {/* —— 3. 头部信息区 —— */}
      <div className="relative z-10 flex items-end gap-7 px-12 pt-6 max-md:gap-4 max-md:px-4 max-md:pt-3">
        <div className="w-[186px] shrink-0 overflow-hidden rounded-xl bg-[#141824] shadow-[0_26px_60px_rgba(0,0,0,0.6)] ring-1 ring-white/15 max-md:w-[104px]">
          <PosterImage
            src={item.posterUrl}
            alt={`${item.title} 海报`}
            className="aspect-[2/3] w-full object-cover"
          />
        </div>

        <div className="min-w-0 flex-1 pb-1">
          <p className="text-caption font-semibold uppercase tracking-[0.22em] text-[var(--accent-2)]">
            {source === "douban" ? "豆瓣" : "TMDB"} · {isMovie ? "电影" : "剧集"}
            {item.genres.length > 0 ? ` · ${item.genres.join(" / ")}` : ""}
          </p>
          <h1 className="text-on-image mt-2 text-[38px] font-bold leading-[1.1] tracking-[-0.02em] text-white max-md:mt-1 max-md:text-[21px]">
            {item.title}
          </h1>
          {/* 原名：与中文标题相同就不渲染——国产片在 TMDB 的 original_title
              往往就是中文名本身，照原样渲染会在标题下面重复一行 */}
          {item.originalTitle && item.originalTitle !== item.title && (
            <p className="text-on-image mt-1.5 truncate text-body text-white/55 max-md:mt-0.5 max-md:text-sub">
              {item.originalTitle}
            </p>
          )}

          {/* 元信息行：评分 / 年份 / 规模 / 质量徽章 */}
          <div className="tnum mt-3.5 flex flex-wrap items-center gap-x-3.5 gap-y-2 text-ui text-white/80 max-md:mt-2 max-md:gap-x-2.5 max-md:gap-y-1 max-md:text-sub">
            {/* 评分 0 = 数据源暂无评分（新片/小众条目），不能照原样渲染成「0.0」
                ——那读起来是「烂到 0 分」。海报卡片与条目详情页都是这个口径 */}
            {item.rating > 0 && (
              <span className="flex items-center gap-1.5">
                <StarIcon className="size-4 text-[#f5c451]" />
                <span className="text-title-sm font-bold text-white">{item.rating.toFixed(1)}</span>
              </span>
            )}
            {!!item.year && <span>{item.year}</span>}
            {item.extent && <span>{item.extent}</span>}
            {info && info.directors.length > 0 && (
              <span className="text-white/65">
                {isMovie ? "导演" : "主创"} {info.directors.join(" / ")}
              </span>
            )}
            {item.badges.length > 0 && (
              <span className="flex gap-1.5">
                {item.badges.map((b) => (
                  <span
                    key={b}
                    className="rounded border border-white/25 px-1.5 py-px text-micro font-semibold tracking-wide text-white/85"
                  >
                    {b}
                  </span>
                ))}
              </span>
            )}
          </div>

          {/* 外部信息源：本条目在数据源站点上的词条地址，用于核对信息或看更多。
              与条目详情页同一处理——压到元信息行下方的小字，不与主操作争视觉权重 */}
          <div className="mt-3 flex flex-wrap items-center gap-x-3.5 gap-y-1">
            {source === "tmdb" && (
              <SourceLink
                href={`https://www.themoviedb.org/${item.type}/${item.id}`}
                label="TMDB"
              />
            )}
            {info?.sourceUrl && (
              <SourceLink href={doubanAppHref ?? info.sourceUrl} label="豆瓣" />
            )}
          </div>

          {/* 在库是详情状态，不与外部词条链接混在一起；每个链接保持后端给出的库顺序。 */}
          {libraryLinks.length > 0 && (
            <div className="mt-4 max-w-full max-md:mt-3">
              <div className="inline-flex max-w-full flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-emerald-300/20 bg-emerald-400/[0.08] px-3.5 py-2.5 text-sub text-emerald-100/90 max-md:px-3">
                <span className="flex shrink-0 items-center gap-1.5 font-medium text-emerald-200">
                  <FolderIcon className="size-4" />
                  在库
                </span>
                {libraryLinks.map((libraryLink) => (
                  <Link
                    key={`${libraryLink.libraryId}:${libraryLink.mediaItemId}`}
                    href={`/library/${libraryLink.libraryId}/item/${libraryLink.mediaItemId}?returnTo=${encodeURIComponent(libraryReturnTo)}` as Route}
                    className="min-w-0 max-w-full break-words font-medium text-emerald-100 underline decoration-emerald-200/45 underline-offset-4 transition-colors hover:text-white hover:decoration-emerald-100"
                  >
                    {libraryLink.libraryName}
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* 操作区：已订阅的影片主按钮变为状态展示（点击进入管理弹层可取消订阅） */}
          <div className="mt-5 flex flex-wrap items-center gap-3 max-md:mt-3.5 max-md:gap-2">
            {sub ? (
              <button
                type="button"
                onClick={openSubscribe}
                className="btn-glass flex h-10 items-center gap-2 bg-white/10 px-5 text-ui font-medium backdrop-blur-md transition hover:bg-white/15"
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
                className="btn-accent flex h-10 items-center gap-2 rounded-full px-5 text-ui font-semibold"
              >
                <BellIcon className="size-4" />
                订阅追踪
              </button>
            )}
            {/* 搜索资源：不订阅、只想手动找种子下一次的直达口（此前只能回 ⌘K 重打片名） */}
            <Link
              href={`/search?q=${encodeURIComponent(item.title)}` as Route}
              className="btn-glass flex h-10 items-center gap-2 bg-white/10 px-5 text-ui font-medium backdrop-blur-md transition hover:bg-white/15"
            >
              <SearchIcon className="size-4" />
              搜索资源
            </Link>
            {sub && (
              <span className="text-on-image flex items-center gap-1.5 text-sub text-[var(--text-muted)]">
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
        onChanged={refreshSubscriptions}
      />

      {/* —— 4~6. 剧情简介 / 演职员 / 词条信息 —— */}
      <div className="mt-9 space-y-8 px-12 max-md:mt-6 max-md:space-y-6 max-md:px-4">
        {item.overview && (
          <section>
            <h2 className="text-on-image mb-3 text-body-lg font-semibold tracking-[-0.01em] text-[var(--text)]">
              剧情简介
            </h2>
            <p className="text-on-image max-w-3xl text-body leading-7 text-white/78">
              {item.overview}
            </p>
          </section>
        )}

        {/* 演职员：与条目详情页同一套横滚版式，头像来自 TMDB credits / 豆瓣 actors */}
        {info && <CastRow cast={info.cast} />}

        {/* 词条信息：一格都没有时整段不渲染。把导演与主演移走之后，小众条目
            很容易落到「每一格都是空」的状态，那时候留下的是一个空玻璃盒子 */}
        {hasFacts && info && (
          <section>
            <h2 className="text-on-image mb-3 text-body-lg font-semibold tracking-[-0.01em] text-[var(--text)]">
              词条信息
            </h2>
            <div className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-6 backdrop-blur-xl max-md:p-4">
              {/* 「导演」已经在元信息行、「主演」已经由上面的演职员条完整呈现，
                  这里不再重复成一行文字——词条信息只留它们之外的档案字段 */}
              <dl className="grid gap-x-10 gap-y-5 sm:grid-cols-2 xl:grid-cols-3">
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

      {/* —— 7. 剧照与海报 —— */}
      {detail && (detail.backdrops.length > 0 || detail.posters.length > 0) && (
        <div className="mt-9 px-12 max-md:mt-6 max-md:px-4">
          <PhotoWall
            title={item.title}
            backdrops={detail.backdrops}
            posters={detail.posters}
          />
        </div>
      )}

      {/* —— 8. 相似推荐 —— */}
      {related.length > 0 && (
        <div className="mt-9">
          <MediaRow row={{ id: `related-${item.id}`, title: "相似推荐", items: related }} />
        </div>
      )}
      </div>
      </div>
    </div>
  );
}

/** 外部信息源链接：弱化的小字，与条目详情页同款 */
function SourceLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-sub text-[var(--text-faint)] underline-offset-4 transition-colors hover:text-[var(--text)] hover:underline"
    >
      {label} ↗
    </a>
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
  const edgeFrame = useRef(0);

  /** 与 HScroller 同款的边缘检测：到达两端时隐藏对应方向的翻页钮。
   *  测量合并进 rAF：scroll 回调里直接读布局会强制同步布局（见 h-scroller.tsx） */
  const updateEdges = useCallback(() => {
    if (edgeFrame.current) return;
    edgeFrame.current = window.requestAnimationFrame(() => {
      edgeFrame.current = 0;
      const el = scrollerRef.current;
      if (!el) return;
      setCanLeft(el.scrollLeft > 1);
      setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    });
  }, []);

  useEffect(() => {
    updateEdges();
    window.addEventListener("resize", updateEdges);
    return () => {
      window.removeEventListener("resize", updateEdges);
      window.cancelAnimationFrame(edgeFrame.current);
    };
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
        <h2 className="text-on-image text-body-lg font-semibold tracking-[-0.01em] text-[var(--text)]">
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
                className={`tnum rounded-full px-3 py-1 text-sub font-medium transition-colors ${
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
                active.id === "backdrops" ? "aspect-video h-[148px] max-md:h-[104px]" : "aspect-[2/3] h-[148px] max-md:h-[126px]"
              }`}
            >
              <PosterImage
                src={img.previewUrl}
                alt={`${title} ${active.label}`}
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
    // ambient-fallback：本页豁免了全局蒙版（isHome），而此刻还没有沉浸背景可铺，
    // 文案会直接压在用户配的壁纸上——亮壁纸下就读不出来了，兜底态自己带一层底。
    // flex-1 而非 h-full：调用处在其上方叠了一条 PageNav（flex 纵向布局）。
    <div className="ambient-fallback flex flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
      {failed ? (
        <>
          <p className="text-body-lg font-semibold text-[var(--text)]">未能加载该影片详情</p>
          <p className="max-w-sm text-ui leading-6 text-[var(--text-muted)]">
            资源可能已下线，或网络暂时不可达。请返回后重试。
          </p>
          <button
            type="button"
            onClick={onBack}
            className="btn-glass px-4 py-2 text-ui font-medium text-[var(--text)]"
          >
            <ArrowLeftIcon className="size-4" />
            返回
          </button>
        </>
      ) : (
        <div className="flex items-center gap-2.5 text-ui text-[var(--text-muted)]">
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
      <dt className="text-sub text-[var(--text-faint)]">{label}</dt>
      <dd className="mt-1 text-ui leading-6 text-[var(--text)]">{value}</dd>
    </div>
  );
}
