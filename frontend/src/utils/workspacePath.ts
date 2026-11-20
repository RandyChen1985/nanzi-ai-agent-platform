/**
 * 工作区路径的纯计算工具（不发请求、不碰 DOM）。
 *
 * 单独成文件而不是塞进 `workspaceFilePreview.ts`：那边 import 了 axios，
 * 任何要验证路径逻辑的 Node 探针都会被 `require('axios')` 挡住，只能退化成
 * 「读源码做字符串断言」——路径算错的代价是跳到不存在的目录，值得真跑一遍。
 */

/**
 * 取路径的父目录。
 *
 * 使用场景：`copy-to-workspace` 不指定 `target_dir` 时副本落在**本人工作区根**，
 * 而触发「复制并分享」的场景恰恰是用户正在浏览公共目录——刷新当前目录永远看不到
 * 副本，必须跳到副本所在目录。管理页与浏览器抽屉两处都要算这个父目录，
 * 各写一份 `slice` 迟早算出不一样的结果。
 *
 * 边界：返回空串表示已到顶层（工作区虚拟根）。
 *   `/a.md`       -> `''`
 *   `/dir/a.md`   -> `/dir`
 *   `/dir/a.md/`  -> `/dir`   （容忍尾斜杠）
 *   `a.md`        -> `''`     （相对路径没有父目录信息）
 *   `''`          -> `''`
 */
export function parentDirectoryOf(path: string): string {
  const trimmed = path.replace(/\/+$/, '')
  const idx = trimmed.lastIndexOf('/')
  return idx <= 0 ? '' : trimmed.slice(0, idx)
}
