import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";
import { fileViewerRenderers } from "@file-viewer/vite-plugin";

// 构建标识：优先取部署时注入的 APP_VERSION，否则用构建时间。
// 用途是排查「线上跑的是不是这次构建」——此前嵌入页把版本号硬编码在日志里
// （`App Version: 2026-01-20-v1`），早已过期且没人维护，反而会误导排查。
const BUILD_VERSION =
  process.env.APP_VERSION || `build-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "")}`;

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  return {
    // fileViewerRenderers 自动发现已安装的 @file-viewer/preset-*，
    // 并把 Worker / WASM / 字体资产复制到 public/file-viewer/（dev 与 build 均生效）
    //
    // inject: false —— 默认行为是把「渲染器注册模块」注入 **HTML 入口**，结果是
    // index.html 会 modulepreload 全部渲染器（pdf/pdf-worker/spreadsheet/word/ofd/
    // archive…，gzip 合计约 1MB）。而文件预览只在画布的 DocumentViewer 里用到，
    // 且该组件本身就是懒加载：改由它显式 import 虚拟模块，首屏就不再为「可能打开的
    // 文档」预下载整套渲染器。嵌入挂件（iframe）对这个差别尤其敏感。
    plugins: [vue(), fileViewerRenderers({ copyAssets: true, inject: false })],
    define: {
      __APP_VERSION__: JSON.stringify(BUILD_VERSION),
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    esbuild: {
      drop: mode === "production" ? ["console", "debugger"] : [],
    },
    server: {
      port: 5173,
      proxy: {
        // 开发环境：将所有 /api、/mcp、/docs、/openapi.json、/.well-known 请求代理到后端
        // 生产环境：前后端同端口 8001，不需要代理
        "^/(api|mcp|docs|openapi\\.json|\\.well-known)": {
          target: "http://localhost:8001",
          changeOrigin: true,
        },
      },
    },
  };
});
