"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { CheckIcon } from "@/components/icons";
import { PosterImage } from "@/components/poster-image";
import { listLibraries, type MediaLibrary } from "@/lib/api/libraries";
import {
  createSubscription,
  deleteSubscription,
  listRuleSets,
  prepareSubscription,
  type PrepareResult,
  type ResolveCandidate,
  type RuleSet,
  type SeasonOverview,
} from "@/lib/api/subscriptions";
import { cachedImageUrl } from "@/lib/image-proxy";
import type { MediaType } from "@/lib/media-types";

/**
 * 订阅弹层的打开参数：TMDB 入口带 tmdbId；豆瓣入口带 doubanId + title(+year)，
 * 由后端收敛到 TMDB 锚（歧义时本弹层内让用户从候选中确认一次）。
 */
export interface SubscribeTarget {
  kind: MediaType;
  source: "tmdb" | "douban";
  tmdbId?: number;
  doubanId?: string;
  title: string;
  year?: number;
}

/**
 * 订阅弹层：一次点击完成订阅，复杂度沉到默认值。
 *
 * 流程（对应后端 /subscriptions/prepare 的三态）：
 *   loading → ready（渲染季选择 + 追新开关 + 规则组）
 *           → ambiguous（豆瓣收敛歧义：候选墙确认一次后重新 prepare）
 *           → not_found（TMDB 未收录，无法订阅）
 * 已订阅的条目进入管理态：展示状态并提供取消订阅。
 *
 * 默认值策略：剧集默认勾选全部已播出的正季（特别季 0 须手动勾）、
 * 在播剧默认打开「持续追新」；规则组默认选中系统默认组。
 */
export function SubscribeDialog({
  target,
  onClose,
  onChanged,
}: {
  target: SubscribeTarget | null;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [prepared, setPrepared] = useState<PrepareResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([]);
  const [libraries, setLibraries] = useState<MediaLibrary[]>([]);
  const [selectedSeasons, setSelectedSeasons] = useState<Set<number>>(new Set());
  const [followFuture, setFollowFuture] = useState(false);
  const [ruleSetId, setRuleSetId] = useState<number | null>(null);
  const [libraryId, setLibraryId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  /** 预检并按结果初始化表单默认值（候选确认后会带着 tmdbId 再次进入）。 */
  const runPrepare = useCallback(
    async (t: SubscribeTarget) => {
      setPrepared(null);
      setError(null);
      try {
        const [result, rules, libs] = await Promise.all([
          prepareSubscription(
            t.source === "douban" && !t.tmdbId
              ? {
                  source: "douban",
                  kind: t.kind,
                  title: t.title,
                  year: t.year,
                  douban_id: t.doubanId,
                }
              : { source: "tmdb", kind: t.kind, tmdb_id: t.tmdbId, douban_id: t.doubanId },
          ),
          listRuleSets(),
          listLibraries(t.kind),
        ]);
        setRuleSets(rules);
        setRuleSetId(rules.find((r) => r.is_default)?.id ?? rules[0]?.id ?? null);
        setLibraries(libs);
        setLibraryId(libs.find((l) => l.is_default)?.id ?? libs[0]?.id ?? null);
        setPrepared(result);
        // 默认勾选全部已播出的正季；在播剧默认追新
        const airedSeasons = result.seasons
          .filter((s) => s.season_number > 0 && s.aired_count > 0)
          .map((s) => s.season_number);
        setSelectedSeasons(new Set(airedSeasons));
        setFollowFuture(
          t.kind === "tv" && result.media?.status === "Returning Series",
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : "预检失败，请稍后重试");
      }
    },
    [],
  );

  useEffect(() => {
    if (target) void runPrepare(target);
  }, [target, runPrepare]);

  // Esc 关闭
  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, onClose]);

  const toggleSeason = (n: number) =>
    setSelectedSeasons((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });

  const pickCandidate = (candidate: ResolveCandidate) => {
    if (!target) return;
    void runPrepare({ ...target, tmdbId: candidate.tmdb_id });
  };

  const submit = async () => {
    if (!target || !prepared?.media) return;
    setBusy(true);
    setError(null);
    try {
      await createSubscription({
        kind: prepared.media.kind,
        tmdb_id: prepared.media.tmdb_id,
        selected_seasons: [...selectedSeasons].sort((a, b) => a - b),
        follow_future: followFuture,
        rule_set_id: ruleSetId,
        library_id: libraryId,
        douban_id: target.doubanId ?? null,
      });
      onChanged?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "订阅失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  const unsubscribe = async () => {
    if (!prepared?.existing_subscription_id) return;
    setBusy(true);
    try {
      await deleteSubscription(prepared.existing_subscription_id);
      onChanged?.();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消订阅失败");
    } finally {
      setBusy(false);
    }
  };

  const canSubmit = useMemo(() => {
    if (!prepared?.media || busy) return false;
    if (prepared.media.kind === "movie") return true;
    return selectedSeasons.size > 0 || followFuture;
  }, [prepared, busy, selectedSeasons, followFuture]);

  if (!target) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={`订阅《${target.title}》`}
    >
      {/* 遮罩：点击空白处关闭 */}
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />

      <div className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.92)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
        <div className="max-h-[76vh] overflow-y-auto p-6">
          <h2 className="text-[17px] font-bold text-white">
            订阅追踪
            <span className="ml-2 text-[13px] font-normal text-[var(--text-muted)]">
              {target.title}
              {target.year ? ` (${target.year})` : ""}
            </span>
          </h2>

          {/* —— 加载 / 错误 —— */}
          {!prepared && !error && (
            <div className="mt-8 flex items-center justify-center gap-2.5 pb-4 text-[13px] text-[var(--text-muted)]">
              <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
              正在获取条目信息…
            </div>
          )}
          {error && (
            <p className="mt-4 rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2.5 text-[13px] leading-6 text-red-200">
              {error}
            </p>
          )}

          {/* —— 豆瓣收敛：未收录 —— */}
          {prepared?.status === "not_found" && (
            <p className="mt-4 text-[13.5px] leading-6 text-[var(--text-muted)]">
              TMDB 未收录该条目，暂时无法订阅。订阅依赖 TMDB
              的别名与季集数据来匹配站点资源，可尝试在 TMDB 搜索入口确认条目后再订阅。
            </p>
          )}

          {/* —— 豆瓣收敛：多候选确认 —— */}
          {prepared?.status === "ambiguous" && (
            <div className="mt-4">
              <p className="text-[13px] text-[var(--text-muted)]">
                找到多个可能的条目，请确认你订阅的是哪一部：
              </p>
              <div className="mt-3 grid grid-cols-4 gap-3">
                {prepared.candidates.map((c) => (
                  <button
                    key={c.tmdb_id}
                    type="button"
                    onClick={() => pickCandidate(c)}
                    className="group text-left"
                  >
                    <div className="aspect-[2/3] overflow-hidden rounded-lg bg-[#141824] ring-1 ring-white/10 transition group-hover:ring-white/40">
                      <PosterImage
                        src={c.poster_url ? cachedImageUrl(c.poster_url) : undefined}
                        alt={c.title}
                        className="size-full"
                      />
                    </div>
                    <p className="mt-1.5 truncate text-[12px] text-white/90">{c.title}</p>
                    <p className="truncate text-[11px] text-[var(--text-faint)]">
                      {c.year ?? "年份未知"}
                    </p>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* —— 已订阅：管理态 —— */}
          {prepared?.status === "ready" && prepared.existing_subscription_id && (
            <div className="mt-4">
              <p className="flex items-center gap-2 text-[13.5px] text-white/85">
                <CheckIcon className="size-4 text-[#4ade80]" />
                该{target.kind === "movie" ? "电影" : "剧集"}已在订阅中，movieclaw
                正在持续追踪资源。
              </p>
              <div className="mt-5 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={onClose}
                  className="btn-glass h-9 px-4 text-[13px] font-medium"
                >
                  好的
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={unsubscribe}
                  className="h-9 rounded-full border border-red-400/30 bg-red-500/10 px-4 text-[13px] font-medium text-red-200 transition hover:bg-red-500/20 disabled:opacity-50"
                >
                  取消订阅
                </button>
              </div>
            </div>
          )}

          {/* —— 订阅表单 —— */}
          {prepared?.status === "ready" && !prepared.existing_subscription_id && (
            <div className="mt-4 space-y-5">
              {prepared.movie_owned && (
                <p className="flex items-center gap-2 rounded-xl border border-[#4ade80]/25 bg-[#4ade80]/10 px-3.5 py-2.5 text-[12.5px] text-[#4ade80]">
                  <CheckIcon className="size-4 shrink-0" />
                  媒体库里已有这部电影，订阅后不会重复下载
                </p>
              )}
              {prepared.media?.kind === "tv" && (
                <section>
                  <h3 className="mb-2 text-[13px] font-semibold text-white/85">
                    选择要收录的季
                    <span className="ml-2 font-normal text-[var(--text-faint)]">
                      勾选即要整季（含未播集）
                    </span>
                  </h3>
                  <div className="space-y-1.5">
                    {prepared.seasons.map((s) => (
                      <SeasonRow
                        key={s.season_number}
                        season={s}
                        checked={selectedSeasons.has(s.season_number)}
                        onToggle={() => toggleSeason(s.season_number)}
                      />
                    ))}
                  </div>

                  <label className="mt-4 flex cursor-pointer items-center justify-between rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-3">
                    <span>
                      <span className="block text-[13px] font-medium text-white/90">
                        持续追新
                      </span>
                      <span className="mt-0.5 block text-[11.5px] text-[var(--text-faint)]">
                        之后播出的新集、新一季自动加入追踪
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      checked={followFuture}
                      onChange={(e) => setFollowFuture(e.target.checked)}
                      className="size-4 accent-[var(--accent-2)]"
                    />
                  </label>
                </section>
              )}

              {ruleSets.length > 0 && (
                <section>
                  <h3 className="mb-2 text-[13px] font-semibold text-white/85">资源规则</h3>
                  <select
                    value={ruleSetId ?? undefined}
                    onChange={(e) => setRuleSetId(Number(e.target.value))}
                    className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3.5 py-2.5 text-[13px] text-white/90 outline-none focus:border-white/25 [&>option]:bg-[#181c28]"
                  >
                    {ruleSets.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                        {r.is_default ? "（默认）" : ""}
                      </option>
                    ))}
                  </select>
                </section>
              )}

              {libraries.length > 0 && (
                <section>
                  <h3 className="mb-2 text-[13px] font-semibold text-white/85">入库到</h3>
                  <select
                    value={libraryId ?? undefined}
                    onChange={(e) => setLibraryId(Number(e.target.value))}
                    className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3.5 py-2.5 text-[13px] text-white/90 outline-none focus:border-white/25 [&>option]:bg-[#181c28]"
                  >
                    {libraries.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                        {l.is_default ? "（默认）" : ""}
                      </option>
                    ))}
                  </select>
                  {/* 落盘路径预览：主根/标题 (年份)，与后端推导规则一致 */}
                  {(() => {
                    const lib = libraries.find((l) => l.id === libraryId);
                    if (!lib?.primary_root || !prepared.media) return null;
                    const folder = `${prepared.media.title}${
                      prepared.media.year ? ` (${prepared.media.year})` : ""
                    }`;
                    return (
                      <p className="mt-1.5 truncate text-[11.5px] text-[var(--text-faint)]">
                        将保存到 {lib.primary_root.replace(/\/+$/, "")}/{folder}
                      </p>
                    );
                  })()}
                </section>
              )}

              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={onClose}
                  className="btn-glass h-9 px-4 text-[13px] font-medium"
                >
                  取消
                </button>
                <button
                  type="button"
                  disabled={!canSubmit}
                  onClick={submit}
                  className="btn-accent h-9 rounded-full px-5 text-[13px] font-semibold disabled:opacity-50"
                >
                  {busy ? "正在订阅…" : "确认订阅"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** 季选择行：季名 + 播出进度；未播季弱化显示但可勾（勾了=要整季）。 */
function SeasonRow({
  season,
  checked,
  onToggle,
}: {
  season: SeasonOverview;
  checked: boolean;
  onToggle: () => void;
}) {
  const total = season.episode_count ?? 0;
  const progress =
    season.aired_count >= total && total > 0
      ? `全 ${total} 集已播完`
      : total > 0
        ? `已播 ${season.aired_count}/${total} 集`
        : season.aired_count > 0
          ? `已播 ${season.aired_count} 集`
          : "未播出";
  // 库存提示（媒体库联通）：已有的集不会重复下载
  const owned =
    season.owned_count > 0
      ? season.owned_count >= total && total > 0
        ? "整季已在库"
        : `库里已有 ${season.owned_count} 集`
      : null;
  return (
    <label
      className={`flex cursor-pointer items-center justify-between rounded-xl border px-4 py-2.5 transition ${
        checked
          ? "border-white/20 bg-white/[0.08]"
          : "border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05]"
      }`}
    >
      <span className="flex items-baseline gap-2.5">
        <span className="text-[13px] font-medium text-white/90">
          {season.season_number === 0 ? "特别篇" : `第 ${season.season_number} 季`}
        </span>
        <span className="tnum text-[11.5px] text-[var(--text-faint)]">{progress}</span>
        {owned && (
          <span className="tnum text-[11.5px] font-medium text-[#4ade80]/90">{owned}</span>
        )}
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="size-4 accent-[var(--accent-2)]"
      />
    </label>
  );
}
