/// <reference types="vite/client" />
/// <reference types="@file-viewer/vite-plugin/client" />

/**
 * 构建标识，由 `vite.config.ts` 的 `define` 注入（`APP_VERSION` 或构建时间）。
 * 嵌入页把它挂到 `window.__NANZI_EMBED_VERSION__`，便于线上排查「跑的是哪次构建」。
 */
declare const __APP_VERSION__: string
