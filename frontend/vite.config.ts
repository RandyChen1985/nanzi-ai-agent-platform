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
        // 开发环境：将后端接口与后端静态资源路径代理到后端
        // 生产环境：前后端同端口 8001，不需要代理
        // /branding（品牌图标）、/static/uploads（上传文件）为后端 StaticFiles 挂载路径，
        // 不代理的话 dev 模式下浏览器请求这些相对路径会命中 Vite SPA fallback 返回 index.html，导致图片展示失败
        "^/api|/docs|/openapi.json|/branding|/static/uploads": {
          target: "http://localhost:8001",
          changeOrigin: true,
        },
      },
    },
  };
});
