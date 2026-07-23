import type { Metadata } from "next";
import { Inter } from "next/font/google";

import { publicEnv } from "@/lib/env";

// 先引入液态玻璃组件自带的样式，再引入本项目的全局深色主题（后者可覆盖前者）。
import "@/vendor/liquid-glass/styles.css";
import "./globals.css";

// 拉丁字符/数字用 Inter（更克制、专业）；中文由 PingFang SC 等系统字体承接。
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

/**
 * 全站标题规范：主页（新任务）就是品牌名本身；子页面用「{页面名} · 品牌名」。
 * 静态子页在各自 page.tsx 里 export metadata（走这里的 template）；
 * 数据驱动的子页（库名/片名/会话名/搜索词）由 lib/use-page-title.ts 在
 * 数据就绪后写 document.title，格式与 template 保持一致。
 */
export const metadata: Metadata = {
  title: {
    default: publicEnv.appName,
    template: `%s · ${publicEnv.appName}`,
  },
  description: "Movieclaw 控制台 —— 液态玻璃风格的影视追踪工作台。",
};

/**
 * 防背景闪烁（FOUC）：--backdrop-image 正常由 BackdropProvider 在「挂载 +
 * GET /appearance 返回」之后写入，强刷时首帧会先画出内置默认图再切换、闪一下。
 * 这段内联脚本在首帧绘制前从 localStorage 恢复上次的背景变量（缓存由
 * lib/backdrop.tsx 在每次换图时写入；图片 URL 带版本号且强缓存，恢复是瞬时的）。
 * 只接受站内相对路径，缓存被篡改也注入不了外部地址。
 *
 * 沉浸路由（/runs，Agent 对话页）：首帧绘制前给 <html> 打 immersive-route
 * 标记——背景大图的伪元素整个不渲染（也就不会发起图片请求），纯色层免淡入，
 * 强刷时不会闪出背景图。此时也无需恢复背景变量。客户端路由切换后的同步由
 * AppShell 的 effect 负责（见 components/app-shell.tsx）。
 */
const RESTORE_BACKDROP_SCRIPT = `try{if(location.pathname.indexOf("/runs")===0){document.documentElement.classList.add("immersive-route")}else{var u=localStorage.getItem("movieclaw.backdrop");if(u&&u.charAt(0)==="/")document.documentElement.style.setProperty("--backdrop-image",'url("'+u+'")')}}catch(e){}`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning：body 最前的内联脚本会在水合前就给 <html> 写上
    // --backdrop-image 内联样式（见下方 RESTORE_BACKDROP_SCRIPT），服务端首帧 HTML
    // 里没有这个 style，两边必然不一致。这是「首帧防闪烁」的固有代价，用它抑制这一处
    // 预期内的告警（只作用于 <html> 自身属性，不影响子树里真正的水合问题被暴露）。
    <html lang="zh-CN" className={inter.variable} suppressHydrationWarning>
      <body>
        {/* 必须是 body 最前的同步内联脚本：解析即执行，赶在首帧绘制之前 */}
        <script dangerouslySetInnerHTML={{ __html: RESTORE_BACKDROP_SCRIPT }} />
        {children}
      </body>
    </html>
  );
}
