import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  buildChatSessionMarkdown,
  buildSessionExportFilename,
  formatStepPayload,
} from '../src/utils/chatSessionExport.ts'

const __dirname = dirname(fileURLToPath(import.meta.url))
const chatLogs = readFileSync(resolve(__dirname, '../src/views/ChatLogs.vue'), 'utf8')

assert.equal(formatStepPayload({ a: 1 }), '{\n  "a": 1\n}')

const md = buildChatSessionMarkdown(
  [
    {
      id: 1,
      trace_id: 'trace-abc',
      agent_id: 'agent-1',
      query: '你好',
      summary: '回复内容',
      status: 'success',
      created_at: '2026-07-22T08:00:00.000Z',
      execution_time_ms: 120,
    },
  ],
  {
    'trace-abc': {
      steps: [
        {
          step_number: 1,
          event_type: 'tool_call',
          tool_name: 'search',
          tool_input: { q: 'x' },
          tool_output: 'ok',
          execution_time_ms: 50,
          status: 'success',
        },
      ],
    },
  },
  { agentLabel: '主助手', username: 'admin', conversationId: 'conv-123' },
)

assert.match(md, /^# 聊天会话导出/m)
assert.match(md, /会话 ID.*conv-123/)
assert.match(md, /### 用户提问[\s\S]*你好/)
assert.match(md, /### 执行链路[\s\S]*Step 1/)
assert.match(md, /tool_call/)
assert.match(md, /search/)

assert.match(
  buildSessionExportFilename([], 'conversation-id-xyz'),
  /^chat_session_conversa_/,
)

assert.match(chatLogs, /exportSession/, 'ChatLogs should implement exportSession')
assert.match(chatLogs, /chatSessionExport/, 'ChatLogs should use chat session export util')
assert.match(chatLogs, /element:chat_logs:export/, 'ChatLogs should gate export by permission')

console.log('chatSessionExport.test.ts passed')
