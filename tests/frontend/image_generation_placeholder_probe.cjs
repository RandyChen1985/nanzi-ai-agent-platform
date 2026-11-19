/**
 * 真跑 `utils/imageGenerationPlaceholder.ts`：这是纯函数，可以直接断言行为。
 *
 * 为什么不用字符串断言：这里的判断全是「什么情况下该不该出现占位」——
 * 工具还在跑还是已结束、参数被截断后还能不能画出占位、同一轮重试该取哪一条、
 * 竖图比例有没有算对。这些只有真跑一遍才看得出，读源码看不出来。
 */
const assert = require("assert");
const { loadModule } = require("./ts_module_loader.cjs");

const mod = loadModule("frontend/src/utils/imageGenerationPlaceholder.ts");
const {
  resolveImagePlaceholder,
  sizeToAspectRatio,
  normalizePlaceholderSize,
  normalizeStartedAtMs,
  imagePlaceholderStageText,
  imagePlaceholderDetailText,
} = mod;

const log = (overrides) => ({
  kind: "log",
  id: "1",
  title: "调用工具 · generate_image",
  details: "",
  status: "pending",
  ...overrides,
});

/* ── 1. 基本识别 ───────────────────────────────────────── */
{
  const spec = resolveImagePlaceholder([
    log({ tool_name: "generate_image", tool_args: '{"prompt": "一只猫", "size": "1664x928"}' }),
  ]);
  assert.ok(spec, "pending 的 generate_image 必须认出占位");
  assert.strictEqual(spec.tool, "generate_image");
  assert.strictEqual(spec.size, "1664x928");
  assert.strictEqual(spec.count, 1);
}

/* ── 2. 工具结束后必须消失 ─────────────────────────────── */
{
  const done = resolveImagePlaceholder([
    log({ tool_name: "generate_image", status: "success" }),
  ]);
  assert.strictEqual(done, null, "工具已完成就不能再挂占位，否则会和真图叠在一起");

  const failed = resolveImagePlaceholder([
    log({ tool_name: "generate_image", status: "error" }),
  ]);
  assert.strictEqual(failed, null, "失败交给错误卡片，占位不能赖着不走");
}

/* ── 3. 别的工具不触发 ─────────────────────────────────── */
{
  const other = resolveImagePlaceholder([log({ tool_name: "web_search" })]);
  assert.strictEqual(other, null);
  assert.strictEqual(resolveImagePlaceholder([]), null);
  assert.strictEqual(resolveImagePlaceholder(undefined), null);
}

/* ── 4. 委派子代理：工具挂在内层 ───────────────────────── */
{
  const spec = resolveImagePlaceholder([
    {
      kind: "container",
      id: "c1",
      title: "委派子智能体",
      status: "pending",
      children: [log({ tool_name: "edit_image", tool_args: '{"image": "/tmp/a.png"}' })],
    },
  ]);
  assert.ok(spec, "子代理里跑的改图也要出占位");
  assert.strictEqual(spec.tool, "edit_image");
  assert.strictEqual(spec.size, "", "改图没传尺寸 → 空串，由组件回落方形");
}

/* ── 5. 参数被截断 / 不是 JSON：仍要出占位 ─────────────── */
{
  const truncated = resolveImagePlaceholder([
    log({ tool_name: "generate_image", tool_args: '{"prompt": "很长很长' }),
  ]);
  assert.ok(truncated, "参数解析不了也不能整块吞掉：用户还在等图");
  assert.strictEqual(truncated.size, "");
  assert.strictEqual(truncated.count, 1);
}

/* ── 6. 同一轮重试：取最后一条 ─────────────────────────── */
{
  const spec = resolveImagePlaceholder([
    log({ id: "a", tool_name: "generate_image", status: "error" }),
    log({ id: "b", tool_name: "generate_image", tool_args: '{"size": "1024*1024"}' }),
  ]);
  assert.strictEqual(spec.size, "1024x1024", "重试中的那条才是当前，且尺寸要归一");
}

/* ── 7. 张数收敛 ───────────────────────────────────────── */
{
  const four = resolveImagePlaceholder([
    log({ tool_name: "generate_image", tool_args: '{"n": 4}' }),
  ]);
  assert.strictEqual(four.count, 4);
  const absurd = resolveImagePlaceholder([
    log({ tool_name: "generate_image", tool_args: '{"n": 99}' }),
  ]);
  assert.strictEqual(absurd.count, 4, "超过上限就收敛，别在界面上写 99 张");
}

/* ── 8. 比例与尺寸归一 ─────────────────────────────────── */
{
  assert.strictEqual(sizeToAspectRatio("1664x928"), "1664 / 928");
  assert.strictEqual(sizeToAspectRatio("1024X1024"), "1024 / 1024");
  assert.strictEqual(sizeToAspectRatio(""), "1 / 1", "给不出比例用方形，不能塌成 0");
  assert.strictEqual(sizeToAspectRatio("呵呵"), "1 / 1");
  assert.strictEqual(normalizePlaceholderSize("512 × 512"), "512x512");
  assert.strictEqual(normalizePlaceholderSize("abc"), "");
}

/* ── 9. 开始时刻量级归一 ───────────────────────────────── */
{
  const seconds = 1_732_000_000.5;
  assert.strictEqual(normalizeStartedAtMs(seconds), seconds * 1000);
  assert.strictEqual(normalizeStartedAtMs(1_732_000_000_500), 1_732_000_000_500);
  assert.strictEqual(normalizeStartedAtMs(0), null);
  assert.strictEqual(normalizeStartedAtMs(null), null);
  assert.strictEqual(normalizeStartedAtMs("nope"), null);
}

/* ── 10. 阶段文案：三档 + 长尾安抚 + 后端优先 ──────────── */
{
  const gen = { tool: "generate_image", progressMessage: "" };
  assert.strictEqual(imagePlaceholderStageText(gen, 500), "正在提交请求");
  assert.strictEqual(imagePlaceholderStageText(gen, 6_000), "正在绘制");
  assert.strictEqual(
    imagePlaceholderStageText({ tool: "edit_image", progressMessage: "" }, 6_000),
    "正在按你的要求重绘",
  );
  assert.strictEqual(imagePlaceholderStageText(gen, 50_000), "上游较慢，仍在等待");
  assert.strictEqual(
    imagePlaceholderStageText({ tool: "generate_image", progressMessage: "取回图片 2/4" }, 500),
    "取回图片 2/4",
    "后端给了真实阶段就用它，别用秒表推的盖掉",
  );
}

/* ── 11. 副标题 ────────────────────────────────────────── */
{
  assert.strictEqual(
    imagePlaceholderDetailText({ tool: "generate_image", size: "1664x928", count: 4 }),
    "1664x928 · 4 张",
  );
  assert.strictEqual(
    imagePlaceholderDetailText({ tool: "edit_image", size: "", count: 1 }),
    "跟随原图比例",
  );
  assert.strictEqual(
    imagePlaceholderDetailText({ tool: "generate_image", size: "", count: 1 }),
    "使用默认尺寸",
  );
}

process.stdout.write("image generation placeholder probe: all checks passed\n");
