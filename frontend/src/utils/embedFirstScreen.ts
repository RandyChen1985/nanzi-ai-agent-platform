/**
 * 首屏 / 切会话的骨架屏闸门。
 *
 * 旧实现把骨架屏当成「鉴权中」的指示器：token 一校验通过就立刻撤掉，而「确定会话 →
 * 拉首屏历史 → 还原流式快照」这几步还在后面串行 await。这段空档里 `messages` 仍是空数组，
 * 模板的 `messages.length === 0` 分支必然先渲染欢迎页，历史回来后再整块替换——用户看到
 * 的就是「欢迎页闪一下，然后跳成消息列表」。
 *
 * 因此骨架屏的语义改为「当前线程还没准备好」：只有会话已确定、历史与快照都已落地
 * （或超时兜底）才允许撤除。切会话走同一条路径，否则同样会闪。
 *
 * 保持零依赖，便于 Node 直接跑行为测试。
 */

/** 骨架屏最长停留时间：历史/快照请求挂死时也必须让内容可见。 */
export const FIRST_SCREEN_SKELETON_TIMEOUT_MS = 8000;

/**
 * 骨架最短可见时长。
 *
 * 本地/内网的历史请求可能十几毫秒就返回，骨架只出现一两帧，观感上仍是「闪一下」。
 * 补足这段时长再撤，才像一次真正的加载过渡。
 */
export const THREAD_SKELETON_MIN_VISIBLE_MS = 180;

/**
 * 撤骨架前还需要补多少毫秒，补足骨架的最短可见时长。
 *
 * 返回值 0 表示已经展示够久，可以立即撤除。
 */
export function threadSkeletonRemainingVisibleMs(elapsedMs: number): number {
  if (!Number.isFinite(elapsedMs)) return 0;
  return Math.max(0, THREAD_SKELETON_MIN_VISIBLE_MS - Math.max(0, elapsedMs));
}

export interface ThreadSkeletonStartOptions {
  /**
   * 强制显示。
   *
   * 切会话时旧消息已经被清空，若按「有消息就不遮挡」判断会漏掉骨架，清空到历史落地
   * 之间仍会闪欢迎页；这条路径必须强制。
   */
  force?: boolean;
  /** 当前消息列表是否已有内容 */
  hasMessages: boolean;
}

/**
 * 是否要在这次线程加载上显示骨架屏。
 *
 * `hasMessages` 存在的原因：宿主可能重推 `INIT_CONFIG` 让 `initChat` 再跑一遍，
 * 此时界面上已有内容，再盖一层骨架等于把看得见的对话糊掉再闪回来。
 */
export function shouldStartThreadSkeleton(options: ThreadSkeletonStartOptions): boolean {
  if (options.force === true) return true;
  return !options.hasMessages;
}
