/**
 * Cookie 读取工具：把某 URL 下的全部 Cookie 拼成请求头格式。popup 与 background 共用。
 *
 * 用 chrome.cookies.getAll（需目标站点 host 权限）能读到 httpOnly 会话 Cookie，
 * 这是页面 document.cookie 读不到的。
 */

/** 读取指定 URL 下的全部 Cookie，拼成 "name=value; name2=value2" 格式；无则返回空串。 */
export async function readCookieHeader(url: string): Promise<string> {
  const cookies = await chrome.cookies.getAll({ url });
  return cookies.map((c) => `${c.name}=${c.value}`).join('; ');
}
