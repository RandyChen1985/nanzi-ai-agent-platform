#!/usr/bin/env node
"use strict";

/**
 * tests/frontend/stream_throttle_runtime.cjs
 *
 * 流式渲染节流的运行时契约回放器（pytest 中 test_embed_stream_event_runtime 调用）。
 *
 * 背景：EmbedChat.vue 的正文渲染使用「rAF 文本缓冲 + 首片零延迟」策略：
 *   - let pendingContentBuffer = ""; let contentRafId: number | null = null;
 *   - queueContentDelta(piece)：首片（!content && !pendingContentBuffer）直接落屏；
 *     其余累加进 pendingContentBuffer，并用 requestAnimationFrame(flushContentBuffer) 调度。
 *   - flushContentBuffer()：把缓冲经 appendAssistantBodyDelta 合并落屏，并取消挂起的 RAF。
 *   - handleBufferedBodyEvent(data)：retraction 使缓冲失效（丢弃 + cancelAnimationFrame）；
 *     answer/answer_delta 入缓冲；其它有 type 的事件先 flushContentBuffer() 再走 dispatcher。
 *   - 读取循环遇到 [DONE] 置 isChatStreamDone=true、await reader.cancel() 并跳出；
 *     循环结束后与 finally 中均会 flushContentBuffer()，保证终止/异常路径不丢尾字。
 *
 * 本脚本 **不修改任何业务代码**，而是：
 *   1) 校验 frontend/src/views/EmbedChat.vue 仍具备上述实现特征（源码契约）；
 *   2) 用等价纯 JS 复刻上述缓冲/flush 语义，按场景回放 SSE 事件序列；
 *   3) 语义不符时以非 0 退出，并打印失败明细。
 *
 * 用法：node tests/frontend/stream_throttle_runtime.cjs <scenario>
 * 场景：standard | retraction | mixed | promotion | repeated | terminal
 *
 * 说明：本文本序列均为不含内部上下文块的纯文本，因此 EmbedChat 中
 * sanitizeStreamContent() 对本回放的输入等价于恒等变换（该函数只剥离内部上下文块）。
 */

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const EMBED_CHAT_PATH = path.join(REPO_ROOT, "frontend", "src", "views", "EmbedChat.vue");

const DUPLICATE_BODY_GUARD_CHARS = 32; // 与 frontend/src/utils/agentscopeSseHandlers.ts 保持一致

// ---------------------------------------------------------------------------
// 1. appendAssistantBodyDelta 等价实现（复刻 agentscopeSseHandlers.ts:684-744）
// ---------------------------------------------------------------------------

function compactAssistantBody(text) {
  return String(text || "").replace(/\s+/g, "");
}

/** 返回 text 相对 prefix 的可见后缀；prefix 为空则返回 text；不是前缀则返回 null。 */
function visibleSuffixAfterWhitespacePrefix(prefix, text) {
  let i = 0;
  let j = 0;
  const isWs = (ch) => /\s/.test(ch);
  while (i < prefix.length && j < text.length) {
    while (i < prefix.length && isWs(prefix.charAt(i))) i += 1;
    while (j < text.length && isWs(text.charAt(j))) j += 1;
    if (i >= prefix.length || j >= text.length) break;
    if (prefix.charAt(i) !== text.charAt(j)) return null;
    i += 1;
    j += 1;
  }
  while (i < prefix.length && isWs(prefix.charAt(i))) i += 1;
  if (i < prefix.length) return null;
  return text.slice(j);
}

function isDuplicateAssistantBodyDelta(existing, piece) {
  if (!existing || !piece) return false;
  const existingCompact = compactAssistantBody(existing);
  const pieceCompact = compactAssistantBody(piece);
  if (!existingCompact || !pieceCompact) return false;
  if (pieceCompact.length >= DUPLICATE_BODY_GUARD_CHARS && existingCompact.includes(pieceCompact)) {
    return true;
  }
  if (existingCompact.length >= DUPLICATE_BODY_GUARD_CHARS && pieceCompact.includes(existingCompact)) {
    return visibleSuffixAfterWhitespacePrefix(existing, piece) == null;
  }
  return false;
}

// ---------------------------------------------------------------------------
// 2. EmbedChat.vue 流式运行时等价实现
// ---------------------------------------------------------------------------

class StreamRuntime {
  constructor() {
    // agentMsg.value 中被本契约关心的部分
    this.content = "";
    this.isThinking = true;

    // EmbedChat.vue: let pendingContentBuffer / let contentRafId
    this.pendingContentBuffer = "";
    this.contentRafId = null;

    // 确定性 RAF 调度器（浏览器中由渲染帧驱动；这里由场景的 frame 步骤驱动）
    this.rafSeq = 0;
    this.pendingFrame = null;
    this.rafRequests = 0;
    this.frameTicks = 0;

    // 观测点
    this.step = 0;
    this.trace = [];
    this.commitTrace = [];
    this.flushCalls = 0;
    this.dedupSkips = 0;

    // 读取循环状态
    this.readerReadCalls = 0;
    this.readerCancelled = false;
    this.isChatStreamDone = false;
  }

  // --- RAF ---
  requestAnimationFrame(cb) {
    this.rafSeq += 1;
    const id = this.rafSeq;
    this.pendingFrame = { id, cb };
    return id;
  }

  cancelAnimationFrame(id) {
    if (this.pendingFrame && this.pendingFrame.id === id) this.pendingFrame = null;
  }

  hasScheduledFrame() {
    return this.contentRafId !== null && this.pendingFrame !== null;
  }

  /** 浏览器渲染帧：挂起的 RAF 回调在此触发（触发前清空，与浏览器语义一致）。 */
  tickFrame() {
    this.frameTicks += 1;
    const frame = this.pendingFrame;
    if (!frame) return;
    this.pendingFrame = null;
    frame.cb();
  }

  // --- 落屏 ---
  appendAssistantBodyDelta(piece) {
    if (!piece) return false;
    const existing = this.content || "";
    if (existing) {
      const extra = visibleSuffixAfterWhitespacePrefix(existing, piece);
      if (extra !== null) {
        if (!extra.trim()) {
          this.dedupSkips += 1;
          return false;
        }
        piece = extra;
      } else if (isDuplicateAssistantBodyDelta(existing, piece)) {
        this.dedupSkips += 1;
        return false;
      }
    }
    this.content = `${existing}${piece}`;
    return true;
  }

  recordCommit(kind, piece, appended) {
    this.commitTrace.push({
      step: this.step,
      kind,
      piece,
      appended,
      content: this.content,
      frames: this.frameTicks,
    });
  }

  // --- EmbedChat.vue: flushContentBuffer ---
  flushContentBuffer() {
    this.flushCalls += 1;
    if (this.pendingContentBuffer) {
      const buffered = this.pendingContentBuffer;
      this.pendingContentBuffer = "";
      const appended = this.appendAssistantBodyDelta(buffered);
      this.recordCommit("flush", buffered, appended);
    }
    if (this.contentRafId !== null) {
      this.cancelAnimationFrame(this.contentRafId);
      this.contentRafId = null;
    }
  }

  // --- EmbedChat.vue: queueContentDelta ---
  queueContentDelta(piece) {
    if (!piece) return;
    // 首片正文立即显示（零延迟）。
    if (!this.content && !this.pendingContentBuffer) {
      const appended = this.appendAssistantBodyDelta(piece);
      this.recordCommit("first-piece", piece, appended);
      return;
    }
    this.pendingContentBuffer += piece;
    if (this.contentRafId === null) {
      this.rafRequests += 1;
      this.contentRafId = this.requestAnimationFrame(() => this.flushContentBuffer());
    }
  }

  // --- EmbedChat.vue: handleBufferedBodyEvent ---
  handleBufferedBodyEvent(data) {
    if (data.type === "retraction") {
      this.pendingContentBuffer = "";
      if (this.contentRafId !== null) {
        this.cancelAnimationFrame(this.contentRafId);
        this.contentRafId = null;
      }
      return false;
    }
    if (data.type === "answer" || data.type === "answer_delta") {
      const piece = String(data.content || "");
      if (piece) {
        this.queueContentDelta(piece);
        this.isThinking = false;
      }
      return true;
    }
    if (data.type) this.flushContentBuffer();
    return false;
  }

  snapshot() {
    return {
      content: this.content,
      buffer: this.pendingContentBuffer,
      contentRafId: this.contentRafId,
      hasScheduledFrame: this.hasScheduledFrame(),
      frames: this.frameTicks,
      flushCalls: this.flushCalls,
      commits: this.commitTrace.length,
    };
  }
}

// ---------------------------------------------------------------------------
// 3. 事件处理与场景驱动（复刻 EmbedChat.vue 的读取循环与 dispatcher 分支）
// ---------------------------------------------------------------------------

function ev(obj) {
  return JSON.stringify(obj);
}
const delta = (text) => ev({ type: "answer_delta", content: text });
const answer = (text) => ev({ type: "answer", content: text });

/** 处理单条 SSE data 负载；返回 "done" 表示遇到 [DONE]（需 cancel reader 并跳出）。 */
function processEvent(rt, raw) {
  if (raw === "[DONE]") {
    rt.isChatStreamDone = true;
    rt.trace.push({ step: rt.step, kind: "done", buffer: rt.pendingContentBuffer, content: rt.content });
    return "done";
  }

  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    rt.trace.push({ step: rt.step, kind: "parse-error", raw: String(raw) });
    return "parse-error";
  }
  if (data && data.type === "keepalive") return "keepalive";

  const bufferBefore = rt.pendingContentBuffer;
  if (rt.handleBufferedBodyEvent(data)) {
    rt.trace.push({
      step: rt.step,
      kind: "body",
      type: data.type,
      bufferBefore,
      content: rt.content,
      buffer: rt.pendingContentBuffer,
    });
    return "body";
  }

  // 非正文事件：dispatcher 分支（只保留与本契约相关的语义）
  if (data.type === "process_narration_promote") {
    const piece = String(data.content || "");
    if (piece) rt.appendAssistantBodyDelta(piece);
    rt.isThinking = false;
    rt.trace.push({
      step: rt.step,
      kind: "promote",
      bufferBefore,
      content: rt.content,
      buffer: rt.pendingContentBuffer,
    });
    return "promote";
  }
  if (data.type === "retraction") {
    rt.content = String(data.content === undefined || data.content === null ? "" : data.content);
    rt.isThinking = data.final === false;
    rt.trace.push({
      step: rt.step,
      kind: "retraction",
      bufferBefore,
      content: rt.content,
      buffer: rt.pendingContentBuffer,
    });
    return "retraction";
  }
  if (data.type === "run_status" || data.type === "error" || data.type === "duplicate_request") {
    rt.isThinking = false;
  }
  // log / meta / citation / router_log / 其它非正文事件：不改动正文，但必须先 flush 保持顺序。
  rt.trace.push({
    step: rt.step,
    kind: "nonbody",
    type: data.type,
    bufferBefore,
    content: rt.content,
    buffer: rt.pendingContentBuffer,
  });
  return "nonbody";
}

/**
 * 复刻 EmbedChat.vue 的读取循环：
 *   while(true){ const {done,value}=await reader.read(); if(done)break;
 *     for (dataStr of feed(...)) { if([DONE]){isChatStreamDone=true;break;} ... }
 *     if (isChatStreamDone) { await reader.cancel(); break; } }
 *   for (dataStr of parser.flush()) {...}
 *   flushContentBuffer();            // try 正常结束
 *   } catch { flushContentBuffer(); ... } finally { flushContentBuffer(); }
 *
 * steps:
 *   { kind:"read", events:[payload...] } 一次 reader.read() 返回的一整块（含完整 data 行）
 *   { kind:"frame" }                     浏览器渲染帧（触发挂起的 RAF）
 *   { kind:"end" }                       reader.read() -> done（无 [DONE]）
 */
function runScenario(spec) {
  const rt = new StreamRuntime();
  const snaps = [];
  let readerDone = false;

  for (let i = 0; i < spec.steps.length; i++) {
    const step = spec.steps[i];
    rt.step = i;
    if (step.kind === "read") {
      rt.readerReadCalls += 1;
      for (const raw of step.events) {
        if (rt.isChatStreamDone) break; // 与源码一致：同块内 [DONE] 之后的负载被忽略
        processEvent(rt, raw);
      }
      if (rt.isChatStreamDone) {
        rt.readerCancelled = true; // await reader.cancel();
        readerDone = true;
      }
    } else if (step.kind === "frame") {
      rt.tickFrame();
    } else if (step.kind === "end") {
      readerDone = true;
    } else {
      throw new Error(`unknown step kind: ${step.kind}`);
    }
    snaps.push(rt.snapshot());
  }

  if (!readerDone) {
    throw new Error("scenario must terminate with a [DONE] read step or an {kind:'end'} step");
  }

  // parser.flush() 无残留负载；随后 try 尾部与 finally 各 flush 一次。
  rt.flushCallsAtLoopExit = rt.flushCalls;
  rt.flushContentBuffer();
  rt.finalFlushCalls = rt.flushCalls;
  rt.flushContentBuffer(); // finally { flushContentBuffer(); }

  return { rt, snaps, trace: rt.trace };
}

// ---------------------------------------------------------------------------
// 4. 场景定义与断言
// ---------------------------------------------------------------------------

function buildScenarios() {
  return {
    // 常规高吐字率流：首片零延迟，后续按帧合并；正文与非正文事件交错不丢字。
    standard: {
      steps: [
        { kind: "read", events: [ev({ type: "log", id: "l1", title: "开始" })] },
        { kind: "read", events: [delta("你")] },
        { kind: "read", events: [delta("好")] },
        { kind: "frame" },
        { kind: "read", events: [delta("，")] },
        { kind: "read", events: [delta("世界")] },
        { kind: "frame" },
        { kind: "read", events: [ev({ type: "citation", citations: [{ id: 1 }] })] },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, check }) {
        check(rt.content === "你好，世界", `standard: content should be 你好，世界, got ${JSON.stringify(rt.content)}`);
        const first = rt.commitTrace[0];
        check(!!first && first.kind === "first-piece", "standard: first piece must be committed through the zero-latency path");
        check(!!first && first.content === "你" && first.frames === 0,
          `standard: first piece must render immediately before any frame, got ${JSON.stringify(first)}`);
        check(snaps[2].content === "你" && snaps[2].buffer === "好",
          `standard: second delta must stay buffered until the frame, got ${JSON.stringify(snaps[2])}`);
        check(rt.rafRequests >= 2, `standard: at least 2 rAF flushes expected, got ${rt.rafRequests}`);
        check(rt.commitTrace.filter((c) => c.kind === "flush").length >= 2,
          "standard: buffered deltas must be committed by rAF flush callbacks");
        check(rt.pendingContentBuffer === "" && rt.contentRafId === null && !rt.hasScheduledFrame(),
          "standard: buffer/RAF must be empty after termination");
        check(rt.isChatStreamDone === true && rt.readerCancelled === true,
          "standard: [DONE] must set isChatStreamDone and cancel the reader");
        check(rt.flushCalls > snaps[snaps.length - 1].flushCalls,
          "standard: terminal flush must run after the read loop");
      },
    },

    // 撤回：retraction 前未落屏的草稿必须被丢弃，且不得提交到界面。
    retraction: {
      steps: [
        { kind: "read", events: [delta("你好")] },
        { kind: "read", events: [delta("（草稿）")] },
        { kind: "read", events: [ev({ type: "retraction", content: "全新答案", final: false })] },
        { kind: "read", events: [delta("！")] },
        { kind: "frame" },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, trace, check }) {
        check(rt.content === "全新答案！", `retraction: content should be 全新答案！, got ${JSON.stringify(rt.content)}`);
        check(snaps[2].content === "全新答案", `retraction: retraction input must replace content, got ${JSON.stringify(snaps[2].content)}`);
        check(snaps[1].buffer === "（草稿）" && snaps[1].hasScheduledFrame,
          `retraction: draft must be buffered with a scheduled frame, got ${JSON.stringify(snaps[1])}`);
        check(snaps[2].buffer === "" && snaps[2].contentRafId === null && !snaps[2].hasScheduledFrame,
          `retraction: retraction must drop the buffer and cancel the RAF, got ${JSON.stringify(snaps[2])}`);
        check(!rt.commitTrace.some((c) => c.content.includes("（草稿）")),
          "retraction: retracted draft must never be committed to the rendered content");
        check(!rt.content.includes("（草稿）"), "retraction: final content must not contain the retracted draft");
        const retractionTrace = trace.find((t) => t.kind === "retraction");
        check(!!retractionTrace && retractionTrace.buffer === "",
          "retraction: buffer must be invalidated before the content replacement is applied");
        check(snaps[3].buffer === "！" && snaps[4].content === "全新答案！",
          "retraction: post-retraction deltas must buffer then flush normally");
        check(rt.readerCancelled === true, "retraction: [DONE] must release the reader");
      },
    },

    // 混流：正文与非正文事件交错时必须先 flush，保持 SSE 顺序且不丢字/不重复。
    mixed: {
      steps: [
        { kind: "read", events: [delta("A")] },
        { kind: "read", events: [delta("B")] },
        { kind: "read", events: [ev({ type: "log", id: "l2", title: "处理中" })] },
        { kind: "read", events: [delta("C")] },
        { kind: "frame" },
        { kind: "read", events: [ev({ type: "meta", agent_name: "分析师" })] },
        { kind: "read", events: [delta("D")] },
        { kind: "read", events: [ev({ type: "run_status", status: "success" })] },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, trace, check }) {
        check(rt.content === "ABCD", `mixed: content should be ABCD, got ${JSON.stringify(rt.content)}`);
        const nonBody = trace.filter((t) => t.kind === "nonbody");
        check(nonBody.length === 3, `mixed: expected 3 non-body events, got ${nonBody.length}`);
        // 步 2 的 log 到达时缓冲里还有 "B"，必须先 flush 再处理该事件。
        check(nonBody[0].bufferBefore === "B" && nonBody[0].buffer === "" && nonBody[0].content === "AB",
          `mixed: non-body event must flush pending body first, got ${JSON.stringify(nonBody[0])}`);
        check(snaps[2].content === "AB" && snaps[2].buffer === "",
          `mixed: buffer must be flushed by the interleaved log event, got ${JSON.stringify(snaps[2])}`);
        // 步 7 的 run_status 到达时缓冲里还有 "D"。
        check(nonBody[2].bufferBefore === "D" && nonBody[2].buffer === "" && nonBody[2].content === "ABCD",
          `mixed: run_status must flush pending body first, got ${JSON.stringify(nonBody[2])}`);
        check(nonBody[1].bufferBefore === "" && nonBody[1].content === "ABC",
          `mixed: frame-followed meta must observe an empty buffer, got ${JSON.stringify(nonBody[1])}`);
        check(rt.content.indexOf("A") === 0 && rt.content.lastIndexOf("D") === rt.content.length - 1,
          "mixed: body order must be preserved end-to-end");
        check(rt.pendingContentBuffer === "" && rt.contentRafId === null,
          "mixed: no pending buffer or RAF may survive termination");
      },
    },

    // 过程叙述提升为正文：先 flush 既有缓冲，再立即追加提升片段（顺序不可颠倒、不得被推迟到下一帧）。
    promotion: {
      steps: [
        { kind: "read", events: [delta("开头")] },
        { kind: "read", events: [delta(" 缓冲")] },
        { kind: "read", events: [ev({ type: "process_narration_promote", content: "提升段", agent_name: "规划" })] },
        { kind: "frame" },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, trace, check }) {
        check(rt.content === "开头 缓冲提升段", `promotion: content should be 开头 缓冲提升段, got ${JSON.stringify(rt.content)}`);
        const promote = trace.find((t) => t.kind === "promote");
        check(!!promote, "promotion: promote event must be dispatched");
        check(promote.bufferBefore === " 缓冲",
          `promotion: buffered body must still be pending when promote arrives, got ${JSON.stringify(promote)}`);
        check(promote.buffer === "" && promote.content === "开头 缓冲提升段",
          `promotion: promote must flush buffered body then append promoted text in order, got ${JSON.stringify(promote)}`);
        check(snaps[2].content === "开头 缓冲提升段" && snaps[2].buffer === "" && !snaps[2].hasScheduledFrame,
          `promotion: promoted text must render immediately without a pending frame, got ${JSON.stringify(snaps[2])}`);
        check(rt.content.indexOf("缓冲") < rt.content.indexOf("提升段"),
          "promotion: buffered body must stay ahead of the promoted text");
        check(rt.isThinking === false, "promotion: promote must clear the thinking state");
      },
    },

    // 重复负载：重复的 answer_delta 不得重复落屏；整体重发的 answer 只追加缺失后缀。
    repeated: {
      steps: [
        { kind: "read", events: [delta("你好")] },
        { kind: "read", events: [delta("你好")] },
        { kind: "frame" },
        { kind: "read", events: [delta("世界")] },
        { kind: "frame" },
        { kind: "read", events: [answer("你好世界")] },
        { kind: "frame" },
        { kind: "read", events: [answer("你好世界，欢迎")] },
        { kind: "frame" },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, check }) {
        check(rt.content === "你好世界，欢迎", `repeated: content should be 你好世界，欢迎, got ${JSON.stringify(rt.content)}`);
        check(snaps[2].content === "你好", `repeated: duplicated delta must not be appended twice, got ${JSON.stringify(snaps[2].content)}`);
        check(rt.dedupSkips >= 2, `repeated: at least 2 duplicate payloads must be skipped, got ${rt.dedupSkips}`);
        check(rt.content.split("你好世界").length - 1 === 1,
          `repeated: 你好世界 must appear exactly once, got ${JSON.stringify(rt.content)}`);
        check(rt.content !== "你好世界你好世界", "repeated: full-body resend must not duplicate the visible body");
        check(snaps[9] && snaps[9].content === "你好世界，欢迎" && snaps[9].buffer === "",
          "repeated: buffer must be fully drained at termination");
      },
    },

    // 终止路径：[DONE] 时缓冲中仍有未落屏文本，必须 cancel reader 并在终态 flush 中补齐；无 [DONE] 的自然结束同样不能丢尾字。
    terminal: {
      steps: [
        { kind: "read", events: [delta("第一")] },
        { kind: "read", events: [delta("片")] },
        { kind: "read", events: [delta("段")] },
        { kind: "read", events: ["[DONE]"] },
      ],
      verify({ rt, snaps, trace, check }) {
        check(rt.isChatStreamDone === true, "terminal([DONE]): isChatStreamDone must be set");
        check(rt.readerCancelled === true, "terminal([DONE]): await reader.cancel() must release the reader");
        const doneTrace = trace.find((t) => t.kind === "done");
        check(!!doneTrace && doneTrace.buffer === "片段",
          `terminal([DONE]): buffered tail must still be pending at [DONE], got ${JSON.stringify(doneTrace)}`);
        check(rt.content === "第一片段",
          `terminal([DONE]): terminal flush must not lose the buffered tail, got ${JSON.stringify(rt.content)}`);
        check(snaps[2].content === "第一" && snaps[2].buffer === "片段",
          `terminal([DONE]): deltas must stay buffered before [DONE], got ${JSON.stringify(snaps[2])}`);
        check(rt.readerReadCalls === 4 && snaps.length === 4,
          "terminal([DONE]): the read loop must break out immediately and not read again after [DONE]");
        check(rt.pendingContentBuffer === "" && rt.contentRafId === null && !rt.hasScheduledFrame(),
          "terminal([DONE]): buffer/RAF must be released on the terminal path");
      },
    },
  };
}

/** terminal 场景的无 [DONE] 自然结束分支（同场景第二段回放）。 */
function runTerminalNoDoneBranch(check) {
  const spec = {
    steps: [
      { kind: "read", events: [delta("A")] },
      { kind: "read", events: [delta("B")] },
      { kind: "end" },
    ],
  };
  const { rt, snaps } = runScenario(spec);
  check(rt.isChatStreamDone === false, "terminal(no [DONE]): isChatStreamDone must stay false when the reader ends naturally");
  check(rt.readerCancelled === false, "terminal(no [DONE]): reader.cancel() must not be called without [DONE]");
  check(snaps[1].buffer === "B" && snaps[1].content === "A",
    `terminal(no [DONE]): delta must stay buffered when the stream ends, got ${JSON.stringify(snaps[1])}`);
  check(rt.content === "AB", `terminal(no [DONE]): final flush must not lose the buffered tail, got ${JSON.stringify(rt.content)}`);
  check(rt.pendingContentBuffer === "" && rt.contentRafId === null,
    "terminal(no [DONE]): buffer/RAF must be empty after the terminal flush");
}

// ---------------------------------------------------------------------------
// 5. EmbedChat.vue 源码契约（确保回放器锚定真实实现，而非自说自话）
// ---------------------------------------------------------------------------

const SOURCE_MARKERS = [
  ['let pendingContentBuffer = "";', "content buffer declaration"],
  ["let contentRafId: number | null = null;", "RAF id declaration"],
  ["const flushContentBuffer = () => {", "flush function"],
  ["appendAssistantBodyDelta(agentMsg.value, pendingContentBuffer);", "flush commits buffered text"],
  ["pendingContentBuffer += piece;", "queue accumulates pieces"],
  ["contentRafId = requestAnimationFrame(flushContentBuffer);", "queue schedules a frame flush"],
  ["cancelAnimationFrame(contentRafId);", "flush cancels the pending frame"],
  ["if (!agentMsg.value.content && !pendingContentBuffer) {", "first piece renders with zero latency"],
  ['if (data.type === "retraction") {', "retraction branch"],
  ["if (data.type) flushContentBuffer();", "non-body events flush pending body first"],
  ["if (data.type === \"answer\" || data.type === \"answer_delta\") {", "body events are buffered"],
  ["isChatStreamDone = true;", "[DONE] marks the stream done"],
  ["await reader.cancel();", "[DONE] releases the reader"],
  ["flushContentBuffer();", "safe flush on terminal/exception paths"],
  ["finally {", "finally block flushes"],
];

function verifySourceContract(check) {
  if (!fs.existsSync(EMBED_CHAT_PATH)) {
    check(false, `EmbedChat.vue must exist at ${EMBED_CHAT_PATH}`);
    return;
  }
  const content = fs.readFileSync(EMBED_CHAT_PATH, "utf8");
  for (const [marker, label] of SOURCE_MARKERS) {
    check(content.includes(marker), `EmbedChat.vue is missing ${label}: ${JSON.stringify(marker)}`);
  }

  // flushContentBuffer 必须在流失效函数自身的 try/catch/finally 三条结束路径上都可被调用。
  const streamStart = content.indexOf("let isChatStreamDone = false;");
  const streamEnd = content.indexOf("const BOTTOM_THRESHOLD_PX", streamStart);
  check(streamStart >= 0 && streamEnd > streamStart, "EmbedChat.vue stream loop region must be locatable");
  const streamRegion = streamStart >= 0 && streamEnd > streamStart ? content.slice(streamStart, streamEnd) : "";
  check(/} catch \(e: any\) \{[\s\S]*?flushContentBuffer\(\);/.test(streamRegion),
    "EmbedChat.vue stream catch block must call flushContentBuffer()");
  check(/} finally \{[\s\S]*?flushContentBuffer\(\);/.test(streamRegion),
    "EmbedChat.vue stream finally block must call flushContentBuffer()");
  check(/for \(const dataStr of sseLineParser\.flush\(\)\)[\s\S]*?flushContentBuffer\(\);/.test(streamRegion),
    "EmbedChat.vue must flush the buffer after sseLineParser.flush()");
}

// ---------------------------------------------------------------------------
// 6. main
// ---------------------------------------------------------------------------

function main() {
  const scenarioName = process.argv[2];
  const scenarioNames = Object.keys(buildScenarios());
  if (!scenarioName) {
    console.error(`usage: node tests/frontend/stream_throttle_runtime.cjs <${scenarioNames.join("|")}>`);
    return 2;
  }
  if (!scenarioNames.includes(scenarioName)) {
    console.error(`unknown scenario: ${scenarioName} (expected one of ${scenarioNames.join(", ")})`);
    return 2;
  }

  const failures = [];
  const check = (cond, message) => {
    if (!cond) failures.push(message);
  };

  verifySourceContract(check);

  const spec = buildScenarios()[scenarioName];
  const { rt, snaps, trace } = runScenario(spec);
  spec.verify({ rt, snaps, trace, check });
  if (scenarioName === "terminal") {
    runTerminalNoDoneBranch(check);
  }

  if (failures.length > 0) {
    console.error(`[stream_throttle_runtime] scenario=${scenarioName} FAILED (${failures.length})`);
    for (const failure of failures) console.error(`  - ${failure}`);
    return 1;
  }

  console.log(
    `[stream_throttle_runtime] scenario=${scenarioName} OK ` +
    `(content=${JSON.stringify(rt.content)}, rafRequests=${rt.rafRequests}, frames=${rt.frameTicks}, ` +
    `flushCalls=${rt.flushCalls}, dedupSkips=${rt.dedupSkips})`,
  );
  return 0;
}

process.exit(main());
