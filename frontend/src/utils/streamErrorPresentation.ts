export type StreamErrorAIStatus = "success" | "fallback" | "disabled";

export interface StreamErrorDetail {
  message: string;
  rawError?: string;
  aiStatus: StreamErrorAIStatus;
}

export interface StreamErrorPresentation {
  message: string;
  detail: StreamErrorDetail;
}

interface ErrorMessageState {
  content: string;
  errorDetail?: StreamErrorDetail;
}

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" ? value as UnknownRecord : {};
}

function readText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readAIStatus(value: unknown): StreamErrorAIStatus {
  return value === "success" || value === "fallback" ? value : "disabled";
}

function isTerminalError(payload: UnknownRecord): boolean {
  if (payload.type === "error") return true;
  return payload.status === "error" && payload.type !== "log";
}

export function normalizeStreamError(data: unknown): StreamErrorPresentation {
  const payload = asRecord(data);
  const detailPayload = asRecord(payload.error_detail || payload.errorDetail);
  const message = readText(payload.content) || readText(payload.message) || "未知错误";
  const rawError = readText(detailPayload.raw_error) || readText(detailPayload.rawError);
  const detail: StreamErrorDetail = {
    message,
    ...(rawError ? { rawError } : {}),
    aiStatus: readAIStatus(detailPayload.ai_status || detailPayload.aiStatus),
  };
  return { message, detail };
}

export function applyStreamErrorMessage(
  message: ErrorMessageState,
  data: unknown,
): boolean {
  const payload = asRecord(data);
  if (!isTerminalError(payload)) return false;

  const presentation = normalizeStreamError(payload);
  const previous = message.errorDetail;
  const sameError = previous
    && previous.message === presentation.detail.message
    && previous.rawError === presentation.detail.rawError
    && previous.aiStatus === presentation.detail.aiStatus;
  const marker = `> ✕ **处理未完成**: ${presentation.message}`;

  message.errorDetail = presentation.detail;
  if (sameError || (typeof message.content === "string" && message.content.includes(marker))) {
    return false;
  }

  message.content = `${typeof message.content === "string" ? message.content : ""}\n\n${marker}`;
  return true;
}
