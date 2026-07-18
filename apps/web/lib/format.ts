/**
 * 数值格式化工具。
 *
 * 后端约定只回传原始数值（如字节数），展示格式统一由前端决定，
 * 避免各站点原始文本（"1.5 TB" / "1536GB"）格式不一。
 */

/** 字节数 → 可读体积，如「1.50 TB」「800 GB」。0 也是有效值（显示 0 B）。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 2)} ${units[i]}`;
}

/**
 * 分享率 → 展示文本。null 表示站点未提供（与 0.00 —— 真实无上传 —— 含义不同），
 * 显示为「—」。
 */
export function formatRatio(ratio: number | null): string {
  if (ratio == null) return "—";
  return ratio.toFixed(2);
}

/** 秒数 → 可读时长，如「15 分钟」「1.5 小时」。用于同步间隔这类节奏展示。 */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  const hours = seconds / 3600;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} 小时`;
}

/** 大数 → 中文紧凑格式，如 12345.6 →「1.2万」。用于魔力值这类可能到百万级的数。 */
export function formatCompact(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}
