import { request } from "@/lib/http";

/** 后端统一响应信封（见 movieclaw_api.schemas.response.ApiResponse） */
interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

async function unwrap<T>(promise: Promise<ApiEnvelope<T>>): Promise<T> {
  return (await promise).data;
}

// ---------------------------------------------------------------------------
// 界面偏好：按页面分组的样式设定（见 settings.schemas.UiPreferencesSetting）
// ---------------------------------------------------------------------------
// 全站样式设定集中在一个对象里，应用启动时拉一次（见 lib/ui-prefs.tsx）。
// 新增页面/设定时：这里加类型字段 + DEFAULT_UI_PREFS 加默认值，与后端模型对齐。

/** 侧边栏（液态玻璃面板）的样式偏好，参数含义见 lib/glass.ts。
 *  基底为 LiquidGlassCard 同款材质，默认值即 Card 出厂观感。 */
export interface SidebarUiPrefs {
  /** 玻璃透明程度：0 Card 标准玻璃（默认），1 玻璃完全隐去 */
  transparency: number;
  /** 玻璃明暗：-1 最暗 ~ 1 最亮，默认 0（不加暗不提亮） */
  brightness: number;
  /** 玻璃厚度（边缘曲率带宽度，px）：10~90，默认 32（Card 出厂值） */
  depth: number;
}

/** 全站背景蒙版（.page-scrim）的样式偏好，参数含义见 globals.css 的 .page-scrim。 */
export interface ScrimUiPrefs {
  /** 蒙版高斯模糊半径（px）：0 不模糊、背景清晰透出，越大背景越朦胧，默认 3 */
  blur: number;
  /** 蒙版压暗程度：0 完全不压暗，1 全黑，默认 0.45 */
  dark: number;
}

export interface UiPreferences {
  sidebar: SidebarUiPrefs;
  scrim: ScrimUiPrefs;
}

/** 各页面的默认样式（与后端模型默认值一致），拉取失败时前端以此兜底。 */
export const DEFAULT_UI_PREFS: UiPreferences = {
  sidebar: { transparency: 0, brightness: 0, depth: 32 },
  scrim: { blur: 3, dark: 0.45 },
};

/** 把后端返回的偏好与内置默认逐分组合并：老版本后端（不认识新分组/新字段）
 *  返回的数据会缺项，缺什么补什么的默认值，保证消费者拿到的结构永远完整。 */
function withDefaults(data: Partial<UiPreferences> | null | undefined): UiPreferences {
  return {
    sidebar: { ...DEFAULT_UI_PREFS.sidebar, ...data?.sidebar },
    scrim: { ...DEFAULT_UI_PREFS.scrim, ...data?.scrim },
  };
}

/** 读取全站界面偏好（从未配置的页面返回默认值），存服务端、跨设备一致。 */
export async function fetchUiPreferences(init?: RequestInit): Promise<UiPreferences> {
  return withDefaults(
    await unwrap(request<ApiEnvelope<Partial<UiPreferences>>>("/ui/preferences", init)),
  );
}

/** 整体覆盖式保存界面偏好，返回保存后的值。 */
export async function updateUiPreferences(prefs: UiPreferences): Promise<UiPreferences> {
  return withDefaults(
    await unwrap(
      request<ApiEnvelope<Partial<UiPreferences>>>("/ui/preferences", {
        method: "PUT",
        body: JSON.stringify(prefs),
      }),
    ),
  );
}
