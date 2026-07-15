import type { ChatBIInsightMeta } from "@/types/chatbiInsight";

export function applyChatBIInsightEvent(
  message: { chatbiInsight?: ChatBIInsightMeta; hasDataOutput?: boolean },
  event: any,
): boolean {
  if (event?.type !== "chatbi_insight_meta" || !event.data || event.data.version !== 1) {
    return false;
  }
  message.chatbiInsight = {
    ...event.data,
    actions: Array.isArray(event.data.actions) ? event.data.actions.slice(0, 4) : [],
  } as ChatBIInsightMeta;
  message.hasDataOutput = true;
  return true;
}

