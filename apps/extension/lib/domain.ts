/**
 * 主机名 → 可注册域名 推导。popup 与 background 共用，保持与后端
 * (movieclaw_api.services.site_catalog.registrable_domain) 一致的归并规则。
 */

/** 常见的多段顶级域名，用于正确取"可注册域名"（如 co.uk 需保留三段）。 */
const MULTI_PART_TLDS = new Set([
  'co.uk', 'org.uk', 'gov.uk', 'ac.uk', 'me.uk',
  'com.cn', 'net.cn', 'org.cn', 'gov.cn',
  'com.hk', 'com.tw', 'com.au', 'co.jp', 'co.kr',
]);

/** 由主机名推导可注册域名，例如 kp.m-team.cc → m-team.cc。 */
export function registrableDomain(hostname: string): string {
  // 去掉可能的前导点（cookie 的 domain 常形如 .m-team.cc）与端口
  const host = hostname.replace(/^\./, '').split(':', 1)[0];
  const parts = host.split('.').filter(Boolean);
  if (parts.length <= 2) return parts.join('.');
  const lastTwo = parts.slice(-2).join('.');
  if (MULTI_PART_TLDS.has(lastTwo)) return parts.slice(-3).join('.');
  return lastTwo;
}
