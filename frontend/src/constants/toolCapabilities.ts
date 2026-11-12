/**
 * 工具能力常量（与后端 `ToolRegistry.TOOLS_WITH_INTERNAL_LLM` 同源维护）。
 *
 * 只用于一个判断：该工具的「温度」配置有没有意义。
 * 平台里工具都是纯 Python callable，执行时不产生 LLM 采样；
 * 只有内部自己起一次 LLM 调用的工具（当前只有 read_image 走多模态模型）才吃温度。
 * 给纯函数工具配温度属于空承诺，前端把控件置灰并说明原因。
 */
export const TOOLS_WITH_INTERNAL_LLM: ReadonlySet<string> = new Set(["read_image"]);

/** 工具是否在内部调用 LLM（决定工具高级配置里的温度是否可编辑）。 */
export function toolSupportsInternalLlm(toolName: string): boolean {
  return TOOLS_WITH_INTERNAL_LLM.has(toolName);
}
