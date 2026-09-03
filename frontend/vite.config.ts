import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  return {
    plugins: [vue()],
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
