/**
 * 物理标识符（`ai_agents.name`）重名校验。
 *
 * 该字段在库里是全局唯一索引，撞名时创建接口返回 400。用户在「新建智能体」时
 * 只有提交后才会知道撞名，所以这里提供**输入期预检**：失焦时查一次、提交前再兜底查一次。
 *
 * 判定口径由后端 `GET /agents/name-availability` 统一给出（与创建路径共用同一查询），
 * 前端不自行臆断。
 */
import { ref } from "vue";
import axios from "@/utils/axios";

export interface AgentNameCheckResult {
  ok: boolean;
  message: string;
}

export function useAgentNameAvailability() {
  /** 是否正在请求 */
  const checking = ref(false);
  /** null = 尚未判定；true/false = 服务端结论 */
  const available = ref<boolean | null>(null);
  /** 不可用原因，用于字段下方内联提示 */
  const message = ref("");

  // 并发/乱序保护：只认最后一次请求的结果，避免快速修改时旧响应覆盖新结论
  let requestSeq = 0;

  const reset = () => {
    available.value = null;
    message.value = "";
  };

  const check = async (
    rawName: string,
    options: { excludeAgentId?: string } = {},
  ): Promise<boolean> => {
    const name = String(rawName || "").trim();
    if (!name) {
      available.value = false;
      message.value = "物理标识符不能为空";
      return false;
    }

    const seq = ++requestSeq;
    checking.value = true;
    try {
      const res = await axios.get("/api/portal/agents/name-availability", {
        params: { name, exclude_agent_id: options.excludeAgentId || undefined },
      });
      if (seq !== requestSeq) {
        // 已有更新的请求在飞：丢弃本次结果
        return available.value !== false;
      }
      const data = res.data?.data || {};
      available.value = data.available === true;
      message.value = available.value ? "" : String(data.message || "该标识符不可用，请换一个");
      return available.value;
    } catch {
      // 预检接口不可用时不阻塞提交：后端创建接口是权威兜底（会返回中文 400）
      if (seq === requestSeq) {
        available.value = null;
        message.value = "";
      }
      return true;
    } finally {
      if (seq === requestSeq) {
        checking.value = false;
      }
    }
  };

  /** 提交前兜底：明确撞名时拦截并给出原因；无法判定时放行交由后端裁决。 */
  const ensureAvailable = async (
    rawName: string,
    excludeAgentId?: string,
  ): Promise<AgentNameCheckResult> => {
    const ok = await check(rawName, { excludeAgentId });
    return { ok, message: message.value };
  };

  return { checking, available, message, check, ensureAvailable, reset };
}
