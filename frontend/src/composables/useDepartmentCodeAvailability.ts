/**
 * 部门编码（`ai_agent_departments.code`）重名校验。
 *
 * 编码在库里是唯一索引，撞名时创建/更新接口返回 400。但管理员在输入时并不知道
 * 撞没撞，提交后才被驳回、还要重新找回焦点改一遍，体验很差。这里提供**输入期预检**：
 * 输入停顿后查一次、失焦再查一次、提交前兜底一次。
 *
 * 判定口径完全由后端 `GET /departments/code-available` 给出（与创建路径共用同一个
 * 唯一性查询），前端不自行臆断"大写加下划线大概能过"这类规则——那只会造成前后端
 * 口径分叉：前端说可用、后端却 400。
 *
 * 与 `useAgentNameAvailability` 的关键差异：部门的**纯数字编码**会被后端直接判为
 * 不可用（会与自增 id 混淆，路径参数无法判断按哪个解析），所以这里必须把后端的
 * reason 原样展示，不能自己编文案。
 */
import { ref } from "vue";
import axios from "@/utils/axios";

export interface DepartmentCodeCheckResult {
  ok: boolean;
  message: string;
}

export function useDepartmentCodeAvailability() {
  const checking = ref(false);
  /** null = 尚未判定；true/false = 服务端结论 */
  const available = ref<boolean | null>(null);
  /** 不可用原因，用于字段下方内联提示 */
  const message = ref("");

  // 并发/乱序保护：只认最后一次请求的结果，避免快速修改时旧响应覆盖新结论
  let requestSeq = 0;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  const reset = () => {
    available.value = null;
    message.value = "";
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
  };

  const check = async (
    rawCode: string,
    options: { excludeRef?: string | number | null } = {},
  ): Promise<boolean> => {
    const code = String(rawCode || "").trim();
    if (!code) {
      available.value = false;
      message.value = "部门编码不能为空";
      return false;
    }

    const seq = ++requestSeq;
    checking.value = true;
    try {
      const excludeRef = options.excludeRef;
      const res = await axios.get("/api/portal/departments/code-available", {
        params: {
          code,
          exclude_ref: excludeRef === null || excludeRef === undefined ? undefined : String(excludeRef),
        },
      });
      if (seq !== requestSeq) {
        // 已有更新的请求在飞：丢弃本次结果
        return available.value !== false;
      }
      const data = res.data?.data || res.data || {};
      available.value = data.available === true;
      message.value = available.value ? "" : String(data.reason || "该编码不可用，请换一个");
      return available.value;
    } catch {
      // 预检接口不可用时不阻塞提交：后端创建/更新接口是权威兜底（会返回中文 400）
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

  /** 输入期节流检查：用户还在敲字时不必每个按键都打一次接口。 */
  const checkDebounced = (
    rawCode: string,
    options: { excludeRef?: string | number | null } = {},
    delay = 350,
  ) => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      void check(rawCode, options);
    }, delay);
  };

  /** 提交前兜底：明确撞名时拦截并给出原因；无法判定时放行交由后端裁决。 */
  const ensureAvailable = async (
    rawCode: string,
    excludeRef?: string | number | null,
  ): Promise<DepartmentCodeCheckResult> => {
    const ok = await check(rawCode, { excludeRef });
    return { ok, message: message.value };
  };

  return { checking, available, message, check, checkDebounced, ensureAvailable, reset };
}
