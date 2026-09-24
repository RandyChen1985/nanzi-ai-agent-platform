import assert from "node:assert/strict";
import {
  RUN_STATUS_NUDGE_INTERVAL_MS,
  RUN_STATUS_NUDGE_MAX_ATTEMPTS,
  shouldContinueRunStatusNudge,
} from "../src/utils/runStatusConvergence.ts";

// 1) 后端报告已空闲：不再补验（避免无意义请求）。
assert.equal(shouldContinueRunStatusNudge(false, 0), false);
assert.equal(shouldContinueRunStatusNudge(false, RUN_STATUS_NUDGE_MAX_ATTEMPTS), false);

// 2) 后端仍报告运行中：在额度内继续补验，覆盖 SSE run_status 丢失的窗口。
assert.equal(shouldContinueRunStatusNudge(true, 0), true);
assert.equal(shouldContinueRunStatusNudge(true, 1), true);
assert.equal(shouldContinueRunStatusNudge(true, RUN_STATUS_NUDGE_MAX_ATTEMPTS - 1), true);

// 3) 额度用尽：必须停止，不能无限轮询。
assert.equal(shouldContinueRunStatusNudge(true, RUN_STATUS_NUDGE_MAX_ATTEMPTS), false);
assert.equal(shouldContinueRunStatusNudge(true, RUN_STATUS_NUDGE_MAX_ATTEMPTS + 5), false);

// 4) 总覆盖时长应显著短于 1.5s 轮询间隔，否则补验没有意义。
const totalWindowMs = RUN_STATUS_NUDGE_MAX_ATTEMPTS * RUN_STATUS_NUDGE_INTERVAL_MS;
assert.equal(RUN_STATUS_NUDGE_MAX_ATTEMPTS > 0, true);
assert.equal(totalWindowMs <= 1500, true);

console.log("runStatusConvergence: all assertions passed");
