/**
 * 全产品统一的时间处理入口。
 *
 * 约定：所有需要展示时间的地方都走这里，不要在组件里散落 `new Date().toLocaleString()`
 * 之类的裸写法。好处：
 *   1. 解析口径统一 —— 后端时间统一为**带时区标记**的 ISO 串（naive UTC 序列化时补 +00:00，
 *      见 movieclaw_api/schemas/site.py 的 _serialize_utc），dayjs 会正确按 UTC 解析后转成
 *      浏览器本地时区展示，避免"刚验证完却显示 8 小时前"这类时区错位。
 *   2. 中文文案统一 —— relativeTime 插件配 zh-cn locale，产出"几秒前 / 几分钟前 / 几天前"。
 *
 * dayjs 选型：~2KB，仅按需加载 relativeTime 插件与 zh-cn 语言包，不引入完整 moment。
 */
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/zh-cn";

dayjs.extend(relativeTime);
dayjs.locale("zh-cn");

/**
 * ISO 时间 → 中文相对时间，如「刚刚 / 3 分钟前 / 2 天前」。
 * 用于"上次检查""最近验证"这类关注"多久之前"的场景。
 * 传入 null/空 表示从未发生，返回「从未」。
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "从未";
  return dayjs(iso).fromNow();
}

/**
 * ISO 时间 → 本地绝对时间，如「2026/07/09 18:00」。
 * 用于"令牌生成于""创建于"这类关注"具体是哪一刻"的场景。
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dayjs(iso).format("YYYY/MM/DD HH:mm");
}
