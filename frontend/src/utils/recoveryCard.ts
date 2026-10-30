/**
 * 会话恢复提示卡片的状态决策。
 *
 * EmbedChat 切页面回来时靠 run-status 恢复器 + 事件续显把后台进度收敛回界面。此前提示
 * 只有一条 toast：装不下「后台在不在跑、跑到哪、要不要刷新」，而快照不可用
 * （`activeStreamDraft` 为 null）时续显不会启动，连那条 toast 都不会出现——后台照跑、
 * 界面不动，用户只能手动刷新。
 *
 * 这里把提示升级成可关闭的状态卡片，决策收敛为纯函数：组件只负责渲染。
 */

export interface RecoveryCardInput {
  /** 后端运行锁是否仍持有（run-status 的真相）。 */
  runActive: boolean;
  /** 界面上是否还有一份在途草稿（快照已还原、落库终态未到）。 */
  hasDraft: boolean;
  /** 草稿已渲染的执行步骤数。 */
  draftStepCount: number;
  /**
   * 本次恢复实际补回的事件条数（累计从事件日志拉取并应用到草稿的条数）。
   *
   * 不能拿续显游标（`activeStreamDraft.lastAppliedSeq`）充当这个数：游标是 Redis
   * INCR 分配的事件序号，与日志 list 索引解耦、还受 LTRIM 截断影响，序号会远大于
   * 实际补回的条数——直接展示会让用户以为加载了成倍的量。
   */
  syncedEventCount: number;
  /** 本轮卡片是否已被用户手动关闭。 */
  turnDismissed: boolean;
  /**
   * 本端用户主动点了「停止生成」。
   *
   * 停止只 abort 本地流：流已不在途、在途草稿还在、后端锁尚未释放，三条判据与
   * 「切走页面后 SSE 静默断开」完全同构。没有这一位，界面会先弹「会话仍在后台处理中」，
   * 待取消轮落库（FinalizeStep 仍会把已生成的部分写成 cancelled）后再补一句
   * 「执行已完成，内容已补齐」——用户刚按了停止，这两句都是反向信号。
   */
  turnStoppedByUser?: boolean;
  /** 落库终态是否已写入界面。 */
  terminalApplied: boolean;
  /**
   * 本端 SSE 是否仍在收流。
   *
   * 正常对话（流健康）期间 `runActive` 与在途草稿同时为真，但那是本端在实时接收，
   * 不是「后台在跑而前端断了」的恢复场景，不能弹恢复提示。
   */
  streaming: boolean;
  /**
   * 本轮是否真的因恢复场景展示过卡片。
   *
   * 正常对话结束时同样会「落库终态已写入界面」，但用户全程没离开过页面，弹一句
   * 「内容已补齐」纯属噪音——只有先提示过「后台仍在处理」，补齐才值得再说一声。
   */
  engaged: boolean;
  /** 当前执行阶段摘要（无则省略该段）。 */
  stageLabel?: string;
}

export type RecoveryCardPhase = "hidden" | "syncing" | "done";

export interface RecoveryCardState {
  phase: RecoveryCardPhase;
  message: string;
  detail?: string;
}

const SYNCING_WITH_DRAFT_MESSAGE = "会话仍在后台处理中，已恢复实时同步";
const SYNCING_WITHOUT_DRAFT_MESSAGE = "正在同步后台进度，输出完成后会自动补齐";
const DONE_MESSAGE = "执行已完成，内容已补齐";
const DONE_DETAIL = "已切换到落库的最终结果";

/** 把「阶段 / 步数 / 本轮补回事件数」里已知的部分拼成一行；一个都不知道时留空。 */
const buildDetail = (input: RecoveryCardInput): string | undefined => {
  const parts: string[] = [];
  const stageLabel = String(input?.stageLabel || "").trim();
  if (stageLabel) parts.push(stageLabel);
  const stepCount = Math.max(0, Number(input?.draftStepCount ?? 0) || 0);
  if (stepCount > 0) parts.push(`${stepCount} 步`);
  const syncedEventCount = Math.max(0, Number(input?.syncedEventCount ?? 0) || 0);
  if (syncedEventCount > 0) parts.push(`本轮补回 ${syncedEventCount} 条事件`);
  return parts.length ? parts.join(" · ") : undefined;
};

export function planRecoveryCard(input: RecoveryCardInput): RecoveryCardState {
  // 用户本轮已明确关闭：不再打扰。范围仅限本轮，下一轮提问会复位该标记。
  // 关闭优先于完成态——用户已经表示不想看这张卡片了。
  if (input?.turnDismissed) return { phase: "hidden", message: "" };
  // 用户主动停止 = 本轮不是恢复场景：既不提示「后台仍在处理」，也不在收尾时补一句
  // 「内容已补齐」。收敛（把界面内容对齐到落库结果）照旧静默进行。
  if (input?.turnStoppedByUser) return { phase: "hidden", message: "" };
  // 本端仍在收流 = 这是一次正常对话，不是恢复场景：此时提示「已恢复实时同步」是误报。
  if (input?.streaming) return { phase: "hidden", message: "" };
  // 内容已补齐就是完成态：不必等后端释放锁（锁在落库完成后才释放，存在时间窗）。
  // 但只在本轮确实展示过恢复提示时才收尾，否则正常对话结束也会多一句噪音。
  if (input?.terminalApplied) {
    if (!input?.engaged) return { phase: "hidden", message: "" };
    return { phase: "done", message: DONE_MESSAGE, detail: DONE_DETAIL };
  }
  if (input?.runActive) {
    return {
      phase: "syncing",
      message: input?.hasDraft ? SYNCING_WITH_DRAFT_MESSAGE : SYNCING_WITHOUT_DRAFT_MESSAGE,
      detail: buildDetail(input),
    };
  }
  return { phase: "hidden", message: "" };
}
