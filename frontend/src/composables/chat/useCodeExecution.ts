import { computed, ref } from 'vue';

export type CodeExecutionLanguage = 'python' | 'python3' | 'shell' | 'sh' | 'bash';

export interface CodeExecutionOutput {
  stream: 'stdout' | 'stderr';
  chunk: string;
  sequence?: number;
}

export interface CodeExecutionRequest {
  language: CodeExecutionLanguage | string;
  code: string;
  conversationId?: string | null;
}

type CodeExecutionEvent = {
  event: string;
  data: Record<string, any>;
};

// 凭据由同源 HttpOnly Cookie 自动携带（fetch 默认 same-origin），无需再手工注入。
// 旧版本会从 localStorage 兜底读取，但全站已无写入方，该回退恒为空值。
const getAuthHeaders = (): Record<string, string> => ({
  'Content-Type': 'application/json',
});

const parseSseBlock = (block: string): CodeExecutionEvent | null => {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return { event: 'error', data: { message: '代码执行事件格式无效。' } };
  }
};

export function useCodeExecution() {
  const isRunning = ref(false);
  const executionId = ref<string | null>(null);
  const outputChunks = ref<CodeExecutionOutput[]>([]);
  const status = ref<'idle' | 'running' | 'finished' | 'stopped' | 'timeout' | 'error'>('idle');
  const errorMessage = ref('');
  let abortController: AbortController | null = null;
  let activeConversationId: string | null = null;

  const stdout = computed(() => outputChunks.value.filter((item) => item.stream === 'stdout'));
  const stderr = computed(() => outputChunks.value.filter((item) => item.stream === 'stderr'));

  const resetExecution = () => {
    executionId.value = null;
    outputChunks.value = [];
    status.value = 'idle';
    errorMessage.value = '';
  };

  const handleEvent = (event: CodeExecutionEvent) => {
    if (event.event === 'started') {
      executionId.value = event.data.execution_id || null;
      status.value = 'running';
      return;
    }
    if (event.event === 'output') {
      outputChunks.value.push({
        stream: event.data.stream,
        chunk: event.data.chunk || '',
        sequence: event.data.sequence,
      });
      return;
    }
    if (event.event === 'finished') {
      status.value = 'finished';
      return;
    }
    if (event.event === 'stopped') {
      status.value = 'stopped';
      return;
    }
    if (event.event === 'timeout') {
      status.value = 'timeout';
      errorMessage.value = event.data.message || '代码执行超时（60 秒）。';
      return;
    }
    if (event.event === 'error') {
      status.value = 'error';
      errorMessage.value = event.data.message || '代码执行失败。';
    }
  };

  const runExecution = async (request: CodeExecutionRequest) => {
    if (isRunning.value) return;
    resetExecution();
    activeConversationId = request.conversationId || null;
    isRunning.value = true;
    status.value = 'running';
    abortController = new AbortController();

    try {
      const response = await fetch('/api/v1/chat/code-executions/stream', {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'include',
        signal: abortController.signal,
        body: JSON.stringify({
          language: request.language,
          code: request.code,
          conversation_id: request.conversationId || null,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.message || body.detail || `代码执行请求失败（${response.status}）。`);
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('浏览器不支持实时输出流。');

      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() || '';
        for (const block of blocks) {
          const event = parseSseBlock(block);
          if (event) handleEvent(event);
        }
        if (done) break;
      }
      const finalEvent = parseSseBlock(buffer);
      if (finalEvent) handleEvent(finalEvent);
    } catch (error: any) {
      if (error?.name !== 'AbortError') {
        status.value = 'error';
        errorMessage.value = error?.message || '代码执行失败。';
      }
    } finally {
      isRunning.value = false;
      abortController = null;
    }
  };

  const stopExecution = async () => {
    const currentId = executionId.value;
    if (!currentId) return;
    try {
      await fetch(`/api/v1/chat/code-executions/${encodeURIComponent(currentId)}/stop`, {
        method: 'POST',
        headers: getAuthHeaders(),
        credentials: 'include',
        body: JSON.stringify({ conversation_id: activeConversationId }),
      });
    } finally {
      abortController?.abort();
      status.value = 'stopped';
      isRunning.value = false;
      activeConversationId = null;
    }
  };

  return {
    isRunning,
    executionId,
    outputChunks,
    stdout,
    stderr,
    status,
    errorMessage,
    runExecution,
    stopExecution,
    resetExecution,
  };
}
