/**
 * 会话置顶状态（账号级，存 /api/portal/portal-prefs）。
 *
 * 只负责「状态 + 读写 + 失败回滚」，不碰 UI 与 toast 文案（由页面决定），
 * 这样 EmbedChat 与 AgentDebug 能共用同一份逻辑。
 */
import axios from "axios";
import { ref } from "vue";

import type { PinnedConversationSnapshot } from "./useChatHistoryGroups";

/** 最多置顶的会话数，与后端 MAX_PINNED_CONVERSATIONS 保持一致。 */
export const MAX_PINNED_CONVERSATIONS = 20;

const PREFS_ENDPOINT = "/api/portal/portal-prefs";
const PIN_ENDPOINT = "/api/portal/portal-prefs/pinned-conversations";

export interface TogglePinResult {
  ok: boolean;
  /** 切换后的状态（ok 时有效）。 */
  pinned?: boolean;
  message?: string;
}

export function usePinnedConversations(options: {
  headers?: () => Record<string, string> | undefined;
} = {}) {
  const pinned = ref<PinnedConversationSnapshot[]>([]);

  const isPinned = (conversationId: string) =>
    pinned.value.some((item) => item.conversation_id === conversationId);

  /** 读取偏好。置顶是增强能力：失败只降级为空列表，不能拖垮聊天与历史列表。 */
  const load = async () => {
    try {
      const res = await axios.get(PREFS_ENDPOINT, { headers: options.headers?.() });
      const list = res.data?.data?.pinned_conversations;
      pinned.value = Array.isArray(list) ? list : [];
    } catch (e) {
      pinned.value = [];
    }
  };

  const persist = async (next: PinnedConversationSnapshot[]) => {
    await axios.put(
      PIN_ENDPOINT,
      { conversations: next },
      { headers: options.headers?.() },
    );
  };

  /** 从历史聚合成会话行提取快照（字段与后端 PinnedConversation 对齐）。 */
  const snapshotOf = (item: any): PinnedConversationSnapshot => ({
    conversation_id: String(item?.conversation_id || ""),
    title: String(item?.query || item?.summary || "未命名对话"),
    created_at: item?.created_at ? String(item.created_at) : "",
    agent_name: item?.agent_name ? String(item.agent_name) : "",
    turn_count: Number(item?.turn_count || 0),
  });

  /**
   * 置顶 / 取消置顶。新置顶插到首位（最近置顶在最上）。
   * 乐观更新 + 失败回滚，避免网络抖动让界面与服务端状态分叉。
   */
  const toggle = async (item: any): Promise<TogglePinResult> => {
    const conversationId = String(item?.conversation_id || "");
    if (!conversationId) {
      return { ok: false, message: "该会话缺少 ID，无法置顶" };
    }

    const wasPinned = isPinned(conversationId);
    const previous = [...pinned.value];
    const next = wasPinned
      ? pinned.value.filter((entry) => entry.conversation_id !== conversationId)
      : [snapshotOf(item), ...pinned.value];

    if (next.length > MAX_PINNED_CONVERSATIONS) {
      return {
        ok: false,
        message: `置顶数量已达上限（${MAX_PINNED_CONVERSATIONS}）`,
      };
    }

    pinned.value = next;
    try {
      await persist(next);
      return { ok: true, pinned: !wasPinned };
    } catch (e) {
      pinned.value = previous;
      return { ok: false, message: "置顶失败，请稍后重试" };
    }
  };

  /** 删除会话后清理对应置顶；没有命中时直接返回，不发请求。 */
  const removeMany = async (conversationIds: string[]) => {
    const targets = new Set(conversationIds.map((id) => String(id)));
    const next = pinned.value.filter((entry) => !targets.has(entry.conversation_id));
    if (next.length === pinned.value.length) return;

    const previous = [...pinned.value];
    pinned.value = next;
    try {
      await persist(next);
    } catch (e) {
      // 删除已经成功，置顶回写失败不值得把用户拖回错误态；下次加载以服务端为准。
      pinned.value = previous;
    }
  };

  return { pinned, isPinned, load, toggle, removeMany };
}
