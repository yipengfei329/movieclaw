import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  {
    // vendor/ 是从 liquidglass-oss 内联的第三方源码，按原样保留，不参与本项目 lint。
    ignores: [".next/**", ".next-preview/**", "next-env.d.ts", "vendor/**"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // 发现页海报直接引用 TMDB 图床（由浏览器经用户网络环境访问），
      // 不走 next/image 的服务端优化代理（服务端出网不一定可达该图床），故放行原生 <img>。
      "@next/next/no-img-element": "off",
    },
  },
];

export default eslintConfig;
