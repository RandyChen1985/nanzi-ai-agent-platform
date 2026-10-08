import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";
import { fileViewerRenderers } from "@file-viewer/vite-plugin";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  return {
    // fileViewerRenderers 自动发现已安装的 @file-viewer/preset-*，
    // 并把 Worker / WASM / 字体资产复制到 public/file-viewer/（dev 与 build 均生效）
    plugins: [vue(), fileViewerRenderers({ copyAssets: true })],
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
