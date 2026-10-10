/**
 * 平台后端的成功判定：**两套信封**，必须按信封本身来判，不能只认其中一种。
 *
 * - `/api/v1/**`（含全部 `/chat/*`）走 `StandardResponse`
 *   （见 `app/schemas/response.py`）：`{ code: 200, message: "success", data, timestamp, trace_id, execution_mode }`
 *   —— **没有** `status` 字段；
 * - 门户部分历史接口（`/api/portal/auth/user_apikey`、票据兑换等）走遗留信封：`{ status: "success", data }`。
 *
 * 只认后者会**静默失败**：聊天面的会话归属校验曾写成 `res.data?.status !== "success"`，
 * 在 `/chat/*` 上永远为真，于是三处会话采用点全部退化成「判不出来」——历史点不开、
 * 刷新后像开了新会话。信封是 `any`，类型检查抓不到这类「读不存在的字段」，所以这里
 * 显式写成一个小函数并配行为测试。
 */
export const isApiSuccess = (payload: any): boolean => {
  if (!payload || typeof payload !== "object") return false;
  if (payload.code === 200) return true;
  return payload.status === "success";
};
