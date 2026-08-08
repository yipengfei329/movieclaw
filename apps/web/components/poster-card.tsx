"use client";

import { useEffect, useRef, useState, type MouseEvent } from "react";

import type { Route } from "next";
import Link from "next/link";

import { BellIcon, CheckIcon, DownloadIcon, PlusIcon, StarIcon } from "@/components/icons";
import { PosterImage } from "@/components/poster-image";
import { useSubscribeEntry } from "@/components/subscribe-entry";
import { useMediaDetail } from "@/lib/media-detail";
import type { MediaItem, MediaLibraryStatus, MediaType } from "@/lib/media-types";
import { useMediaQuery } from "@/lib/use-media-query";
import { useTapGuard } from "@/lib/use-tap-guard";

/**
 * 海报卡片：发现页海报墙的最小单元（Netflix 式）。
 *
 * 结构分两层：
 *   - 海报区：2:3 竖版海报，常显「评分徽章（右上）+ 最高清晰度徽章（左上）」；
 *     hover 时整卡上浮、海报放大，底部升起渐变信息层（类型 / 简介 / 订阅影片按钮）。
 *   - 文字区：海报下方常显标题与「年份 · 规模」，保证不 hover 也能扫读海报墙。
 *
 * 全站海报卡只有这一种形态：曾经给榜单行做过「左侧描边大数字」的排名变体，
 * 但发现页里排名行有好几条（豆瓣实时热门/口碑榜/Top 250 …），满屏大数字过于
 * 抢眼、也把行与行的节奏打乱，已整体去掉——榜单的名次由行标题表达即可。
 */
export interface PosterCardProps {
  item: MediaItem;
  /** 悬浮层的操作区变体，默认「订阅影片」 */
  action?: PosterCardAction;
  /** 点击目标覆盖：传入即直接链接到该地址（媒体库「最近添加」行跳
   *  库内条目详情），缺省走发现页详情（useMediaDetail.open） */
  href?: Route;
}

/**
 * 悬浮层操作区的形态，按内容与用户的关系选择：
 * - subscribe：还没拥有（发现页/搜索），给「订阅影片」入口；
 * - follow：已在库的在播剧（还会有新集），给「订阅追新」入口；
 * - backfill：已在库的完结剧但已播集有缺口，给「补齐缺集」入口
 *   （订阅创建按 E−H 跳过库里已有的集，只为缺的集生成工单）；
 * - owned：已在媒体库且无后续动作（电影/完结齐全剧），静态「在库」标识；
 * - none：已订阅但尚未落地（单库页「追踪中」），再给订阅按钮是重复操作，不显示。
 *
 * 前三种都是订阅入口，仅文案/图标不同；该影片已存在订阅时自动切换为
 * 「已订阅」状态徽标（点击进入订阅管理弹层），状态来自 SubscribeEntryProvider
 * 的全站订阅列表，卡片自身不发请求。
 */
export type PosterCardAction = "subscribe" | "follow" | "backfill" | "owned" | "none";

/** 三种订阅入口的文案与图标（已订阅时统一切换为状态徽标，不走这张表）。
 *  订阅是「加入追踪清单」，图标用 +（加入）与 🔔（追新）表意——
 *  不能用播放三角：它承诺的是「点了就能看」，会误导用户以为是播放键。 */
const SUBSCRIBE_ACTION_META = {
  subscribe: { label: "订阅影片", Icon: PlusIcon },
  follow: { label: "订阅追新", Icon: BellIcon },
  backfill: { label: "补齐缺集", Icon: DownloadIcon },
} as const;

/**
 * 海报卡片的最小视觉契约。搜索结果不含年份和类型，缺失字段保持不显示，
 * 但仍复用与发现页完全相同的海报、评分、悬浮信息层和来源标识。
 */
export interface PosterVisualItem {
  id: string;
  title: string;
  rating: number;
  posterUrl: string;
  source?: "tmdb" | "douban";
  /** 电影/剧集；豆瓣轻量搜索结果没有该字段，订阅入口点击时补拉详情识别 */
  type?: MediaType;
  year?: number;
  genres?: string[];
  extent?: string;
  badges?: string[];
  overview?: string;
  libraryStatus?: MediaLibraryStatus | null;
}

export function PosterCard({ item, action, href }: PosterCardProps) {
  // 点击整卡（含 hover 信息层）进入该影片的详情页
  const { open } = useMediaDetail();
  if (href) {
    return <PosterCardVisual item={item} action={action} href={href} />;
  }
  return <PosterCardVisual item={item} action={action} onClick={() => open(item)} />;
}

/** 统一海报视觉组件；传入 onClick 时才渲染为可点击按钮。 */
export function PosterCardVisual({
  item,
  onClick,
  href,
  action = "subscribe",
}: {
  item: PosterVisualItem;
  onClick?: () => void;
  href?: Route;
  action?: PosterCardAction;
}) {
  // 触摸端没有 hover，信息层（类型/简介/订阅操作）原本永远展不开。交互对齐
  // 桌面的两段式：第一次点按只「展开」信息层（等价于鼠标悬停），看清操作后
  // 再点海报才进详情；点卡片外任意处收起。之前的方案是在海报角上常驻一枚
  // 订阅圆键，但图标无文案表意不清，且张张海报都印着按钮、墙面很吵。
  const noHover = useMediaQuery("(hover: none)");
  const [revealed, setRevealed] = useState(false);
  const rootRef = useRef<HTMLElement | null>(null);

  // 展开后点卡片外任意处收起（pointerdown 而非 click：滚动/拖动开始就该收）
  useEffect(() => {
    if (!revealed) return;
    const onDown = (e: globalThis.PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setRevealed(false);
    };
    document.addEventListener("pointerdown", onDown, { capture: true });
    return () => document.removeEventListener("pointerdown", onDown, { capture: true });
  }, [revealed]);

  // 触摸端误触保护：海报墙滑动远多于点击，浏览器原生的 click 抑制拦不住
  // 「滑到边缘」「点一下停惯性」这类手势，统一交给 useTapGuard 判定。
  // Link 分支不传动作，仅靠 preventDefault 拦掉不合格点击的跳转。
  const tapGuard = useTapGuard(
    onClick &&
      (() => {
        if (noHover && !revealed) {
          setRevealed(true);
          return;
        }
        onClick();
      }),
  );
  // Link 分支的「首点展开」：误触判定放行、且信息层未展开时，拦下跳转改为展开
  const onLinkClick = (e: MouseEvent) => {
    tapGuard.onClick(e);
    if (e.defaultPrevented) return;
    if (noHover && !revealed) {
      e.preventDefault();
      setRevealed(true);
    }
  };
  const content = <PosterCardContent item={item} action={action} />;
  const interactiveClass =
    "group/card block w-full cursor-pointer rounded-2xl text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]";
  if (href) {
    return (
      <Link
        href={href}
        ref={rootRef as React.Ref<HTMLAnchorElement>}
        {...tapGuard}
        onClick={onLinkClick}
        data-revealed={revealed}
        className={interactiveClass}
        aria-label={`查看《${item.title}》详情`}
      >
        {content}
      </Link>
    );
  }
  if (!onClick) return <div className="group/card block w-full text-left">{content}</div>;
  return (
    <button
      type="button"
      ref={rootRef as React.Ref<HTMLButtonElement>}
      {...tapGuard}
      data-revealed={revealed}
      className={interactiveClass}
    >
      {content}
    </button>
  );
}

function PosterCardContent({
  item,
  action = "subscribe",
}: {
  item: PosterVisualItem;
  action?: PosterCardAction;
}) {
  const badges = item.badges ?? [];
  const genres = item.genres ?? [];
  const overview = item.overview ?? "";
  return (
    <>
      {/* 海报区（自身 relative：徽章与 hover 信息层都绝对定位在它内部） */}
      <div className="relative aspect-[2/3] w-full overflow-hidden rounded-2xl bg-[#141824] shadow-[0_10px_28px_rgba(0,0,0,0.4)] ring-1 ring-white/[0.08] transition-all duration-300 ease-out group-hover/card:-translate-y-1.5 group-hover/card:shadow-[0_22px_50px_rgba(0,0,0,0.6)] group-hover/card:ring-white/25">
        <PosterImage
          src={item.posterUrl}
          alt={`${item.title} 海报`}
          className="absolute inset-0 size-full transition-transform duration-500 ease-out group-hover/card:scale-[1.06]"
        />

        {/* 左上：资源最高清晰度徽章（无资源信息时不渲染）。
            徽章不用 backdrop-blur：海报墙每张卡 2~3 个模糊合成层会显著放大
            滚动时的 GPU 压力（几百张卡叠加），加实底色观感几乎无差 */}
        {badges[0] && (
          <span className="absolute left-2 top-2 rounded-md bg-black/70 px-1.5 py-0.5 text-micro font-bold tracking-wide text-[var(--accent)]">
            {badges[0]}
          </span>
        )}
        {/* 右上：评分徽章（暂无评分时不渲染，避免展示 0.0） */}
        {item.rating > 0 && (
          <span className="tnum absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-caption font-semibold text-white">
            <StarIcon className="size-3 text-[#f5c451]" />
            {item.rating.toFixed(1)}
          </span>
        )}

        {/* hover 信息层：底部渐变升起，展示类型 / 简介 / 快捷操作。
            触摸端由「首次点按」触发（根节点的 data-revealed，见 PosterCardVisual），
            与桌面 hover 是同一层——手机上不再另设常驻的角落圆键。 */}
        <div className="absolute inset-x-0 bottom-0 translate-y-3 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-3 pb-3 pt-10 opacity-0 transition-all duration-300 ease-out group-hover/card:translate-y-0 group-hover/card:opacity-100 group-data-[revealed=true]/card:translate-y-0 group-data-[revealed=true]/card:opacity-100">
          {genres.length > 0 && (
            <p className="text-caption font-medium text-[var(--accent-2)]">
              {genres.join(" · ")}
            </p>
          )}
          {overview && (
            <p className="mt-1 line-clamp-3 text-caption leading-4 text-white/75">
              {overview}
            </p>
          )}
          {action !== "none" && (
            <div className="mt-2.5 flex items-center gap-2">
              <PosterCardActionButton item={item} action={action} />
            </div>
          )}
        </div>
      </div>

      {/* 文字区：常显标题 + 元信息（压在背景大图上，需 text-on-image 投影保证可读） */}
      <div className="mt-2">
        <p className="text-on-image truncate text-ui font-semibold text-[var(--text)]">
          {item.title}
        </p>
        {/* 始终保留一行元信息，避免在库状态或缺失年份让同一海报墙的卡片高低跳动。 */}
        <p className="text-on-image tnum mt-0.5 flex min-h-4 items-center gap-1.5 truncate text-caption text-[var(--text-muted)]">
          {!!item.year && <span>{item.year}</span>}
          {!!item.year && item.extent && <span>·</span>}
          {item.extent && <span>{item.extent}</span>}
          {item.libraryStatus && (
            <span className="flex shrink-0 items-center gap-1.5 text-emerald-300/90">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              在库
            </span>
          )}
        </p>
      </div>
    </>
  );
}

/**
 * 信息层里的操作键（订阅 / 追新 / 补齐 / 在库标识）。
 *
 * 外层整卡已经是 button / Link，内部不能再嵌 button（HTML 不允许交互元素嵌套），
 * 所以用 role="button" 的 span 承载：preventDefault 拦掉 Link 跳转，
 * stopPropagation 拦掉整卡 onClick，点它就只做订阅这一件事。
 */
function PosterCardActionButton({
  item,
  action,
}: {
  item: PosterVisualItem;
  action: PosterCardAction;
}) {
  const { open: openSubscribe, subscriptionOf } = useSubscribeEntry();
  // 订阅入口类动作（subscribe/follow/backfill）才需要判断订阅状态；owned/none 不查询
  const subscribeMeta =
    action === "subscribe" || action === "follow" || action === "backfill"
      ? SUBSCRIBE_ACTION_META[action]
      : null;
  const existingSub = subscribeMeta ? subscriptionOf(item) : undefined;
  const trigger = () => void openSubscribe(item);
  // 圆键就贴在海报角上，横滑时拇指最容易蹭到，同样过一遍误触判定。
  // Hook 不能放在下面的提前返回之后，故与 trigger 一起提到最前。
  const tapGuard = useTapGuard(trigger);

  if (!subscribeMeta) {
    // 在库标识：非交互，与库存格下方的绿点语言一致。
    return (
      <span className="flex h-7 items-center gap-1.5 rounded-full bg-white/[0.18] px-3 text-caption font-semibold text-white/90">
        <span className="size-1.5 rounded-full bg-[#4ade80]" />
        在库
      </span>
    );
  }

  const label = existingSub
    ? `管理《${item.title}》的订阅`
    : `${subscribeMeta.label}《${item.title}》`;

  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={label}
      onPointerDown={tapGuard.onPointerDown}
      onPointerUp={tapGuard.onPointerUp}
      onPointerCancel={tapGuard.onPointerCancel}
      onClick={(e) => {
        // 无论判定结果如何都先拦住冒泡：这颗键嵌在整卡的 button/Link 里，
        // 放行会让「订阅」顺带把详情页也打开。判定通过与否交给 tapGuard。
        e.preventDefault();
        e.stopPropagation();
        tapGuard.onClick(e);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          trigger();
        }
      }}
      className={
        existingSub
          ? "flex h-7 items-center gap-1.5 rounded-full bg-white/[0.18] px-3 text-caption font-semibold text-white/90 transition-colors hover:bg-white/[0.26]"
          : "btn-accent flex h-7 items-center gap-1 rounded-full px-3 text-caption font-semibold"
      }
    >
      {existingSub ? (
        <>
          <CheckIcon className="size-3 text-[#4ade80]" />
          已订阅
        </>
      ) : (
        <>
          <subscribeMeta.Icon className="size-3" />
          {subscribeMeta.label}
        </>
      )}
    </span>
  );
}
