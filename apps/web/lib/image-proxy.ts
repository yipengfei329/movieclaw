import { resolveRequestUrl } from "@/lib/http";

/**
 * 远程静态图片的统一收口入口。
 *
 * 所有 http(s) 绝对地址（TMDB 海报、豆瓣剧照、PT 站图床截图等）一律改走
 * 后端 /images/proxy：后端首次回源抓取后缓存到 data/cache/images，之后同一
 * URL 直接读本地磁盘，不再依赖外网图床的可达性与速度。
 * 非 http(s) 的相对路径（本地上传的背景图等）原样返回，不经代理。
 *
 * 新增图片展示位时请一律经过本函数，不要直接引用远程 URL。
 */
export function cachedImageUrl(url: string): string {
  if (!/^https?:\/\//i.test(url)) return url;
  return resolveRequestUrl(`images/proxy?url=${encodeURIComponent(url)}`);
}
