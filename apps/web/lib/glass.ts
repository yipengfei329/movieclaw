/**
 * 液态玻璃组件的共享入口。
 *
 * vendor/liquid-glass 是从 liquidglass-oss 项目按 MIT 许可内联进来的 WebGL 组件源码
 * （见 vendor/liquid-glass/LICENSE）。这里统一导出常量与类型，方便各处引用。
 *
 * BACKDROP：所有玻璃组件都要传入的背景图 URL。着色器会按元素在视口中的位置
 * 采样这张图做折射，因此它必须与 body 的固定背景是同一张、且同源（放在 public/ 下）。
 */
export const BACKDROP = "/backdrop-default.jpg";

/**
 * 侧栏玻璃 = LiquidGlassCard 的同款材质（用户明确要求「完全还原 Card 质感」）。
 * 这组覆盖逐字段抄自 vendor/LiquidGlassCard.tsx 的 mergedSettings，叠在它的
 * 默认 frosted 变体之上；配合「始终采样背景」（Card 恒为 setBackgroundSampling(true)），
 * 侧栏呈现与 Card 一致的「透光磨砂玻璃 + 微弱材质羽化（opacity .96）」。
 * tint / depth / opacity 三项是设置页滑杆的调节对象，由 sidebarGlass() 注入，
 * 不在此写死。工作台与设置页侧栏共用，改一处即可。
 */
export const SIDEBAR_GLASS = {
  blur: 0.34,
  refraction: 0.38,
  chromaticAberration: 0.025,
  distortion: 0.012,
  edgeHighlight: 0.1,
  specular: 0.12,
  fresnel: 0.9,
  darkTint: 0.12,
  tintStrength: 0.1,
  shadow: 0.18,
  bevel: 0,
} as const;

/** 侧栏玻璃的变体：与 LiquidGlassCard 的默认值一致（frosted 磨砂）。 */
export const SIDEBAR_GLASS_VARIANT = "frosted" as const;

/**
 * 由用户偏好推导侧栏 GlassPanel 的入参（见 lib/api/ui.ts 的 SidebarUiPrefs）。
 * 基底是 Card 同款材质（SIDEBAR_GLASS + frosted 变体 + 始终采样背景），
 * 三根滑杆在其上微调：
 *   - transparency（透明度 0~1）→ opacity = 0.96 × (1 - transparency)：
 *     Card 的材质羽化（shader 的 u_opacity 压整块玻璃的 alpha）。0 = Card
 *     出厂的 0.96（默认），1 = 玻璃完全隐去、原样透出面板之下的真实页面；
 *   - brightness（明暗 -1~1）→ tint：负值向黑压暗、正值向白提亮，
 *     默认 0（Card 出厂值，不加暗不提亮）；
 *   - depth（厚度 10~90，px）→ shader 的 u_zRadius（边缘曲率带宽度）：越大越像
 *     厚玻璃、边缘折射带越宽，默认 32（Card 出厂值）。
 *
 * CSS 侧配套：
 *   - hairlineAlpha 恒为 0：Card 没有任何 CSS 边框，轮廓全靠 shader 的
 *     edgeHighlight / fresnel 边缘光表达，发丝白线会破坏还原度；
 *   - fallbackAlpha：玻璃周边 CSS 装饰（.panel--sidebar 的 WebGL 兜底底色 +
 *     .glass-panel 的外投影）的强度系数，随透明度淡出——玻璃隐去后兜底色
 *     会顶上来挡住页面、外投影会留下面板形状的暗晕。
 * 工作台与设置页两处侧栏共用此推导，保证观感一致。
 */
export function sidebarGlass(prefs: {
  transparency: number;
  brightness: number;
  depth: number;
}) {
  return {
    variant: SIDEBAR_GLASS_VARIANT,
    sampleBackground: 1,
    settings: {
      ...SIDEBAR_GLASS,
      tint: prefs.brightness,
      depth: prefs.depth,
      opacity: 0.96 * (1 - prefs.transparency),
    },
    hairlineAlpha: 0,
    fallbackAlpha: 1 - prefs.transparency,
  };
}
