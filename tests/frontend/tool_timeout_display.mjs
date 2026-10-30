/**
 * 行为测试：挂起中的工具行显示「已耗时 / 超时上限」。
 *
 * 背景（用户截图）：Bash 行右侧只有一个 33.0s，用户不知道还要等多久才算超时。
 * 分母取本轮工具调用的实际超时（版本级优先、否则全局），由 run_config 下发。
 *
 * 这里直接 `node --experimental-strip-types` 加载真实 processTimeline.ts（不复制逻辑），
 * 断言两个用户可见的取舍：分母缺失时不编数字、≥90% 才升级成警示色。
 */
import {
  appendToolTimeoutSuffix,
  isToolTimeoutNearLimit,
} from '../../frontend/src/utils/processTimeline.ts';

const failures = [];
const check = (name, condition, detail = '') => {
  if (!condition) failures.push(detail ? `${name} —— ${detail}` : name);
};

// 1. 有分母就拼上，且按整数秒展示（配置本身只允许整秒）
check('拼上分母', appendToolTimeoutSuffix('33.0s', 180) === '33.0s / 180s');
check('分母取整', appendToolTimeoutSuffix('33.0s', 180.6) === '33.0s / 181s');

// 2. 分母缺失/非法时保持原样：宁可只显示已耗时，也不编一个上限
check('无分母不变', appendToolTimeoutSuffix('33.0s', undefined) === '33.0s');
check('分母为 0 不变', appendToolTimeoutSuffix('33.0s', 0) === '33.0s');
check('分母为负不变', appendToolTimeoutSuffix('33.0s', -180) === '33.0s');
check('分母非数字不变', appendToolTimeoutSuffix('33.0s', Number.NaN) === '33.0s');
check('空文本不加分母', appendToolTimeoutSuffix('', 180) === '');

// 3. 90% 是警示线：162s / 180s 触发，161s 不触发
check('恰好 90% 触发', isToolTimeoutNearLimit(162_000, 180) === true);
check('未到 90% 不触发', isToolTimeoutNearLimit(161_000, 180) === false);
check('刚起步不触发', isToolTimeoutNearLimit(1_000, 180) === false);

// 4. 拿不到已耗时或分母时一律不警示，避免闪烁
check('无已耗时', isToolTimeoutNearLimit(null, 180) === false);
check('无分母', isToolTimeoutNearLimit(200_000, undefined) === false);
check('分母为 0', isToolTimeoutNearLimit(200_000, 0) === false);

if (failures.length > 0) {
  console.error(`tool_timeout_display: ${failures.length} 项失败`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log('tool_timeout_display: all checks passed');
