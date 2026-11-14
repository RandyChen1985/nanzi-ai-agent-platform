<script setup lang="ts">
import { ref } from 'vue'
import { copyToClipboard } from '../../utils/clipboard'

const generationCommand = 'python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"'
const firstSetupExample = JSON.stringify({
  active_key_id: 'cred-v1',
  keys: { 'cred-v1': '<替换为本地命令生成的新密钥>' },
}, null, 2)
const rotationExample = JSON.stringify({
  active_key_id: 'cred-v2',
  keys: { 'cred-v2': '<替换为另行生成的新密钥>' },
}, null, 2)
const copyFeedback = ref('')

async function copyExample(value: string, label: string) {
  try {
    const copied = await copyToClipboard(value)
    copyFeedback.value = copied ? `${label}已复制` : '复制失败，请手动选取下方内容复制。'
  } catch {
    copyFeedback.value = '复制失败，请手动选取下方内容复制。'
  }
}
</script>

<template>
  <details class="rounded-lg border border-gray-200 bg-gray-50 text-xs text-gray-600">
    <summary class="cursor-pointer px-3 py-2 font-medium text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
      如何生成密钥、首次配置与轮换
    </summary>
    <div class="space-y-4 border-t border-gray-200 p-3 leading-relaxed">
      <div class="space-y-2">
        <p>
          密钥必须由安全随机数生成器生成 <strong>32 个原始字节</strong>，再进行 URL-safe Base64 编码。
          不能直接填写任意密码或 32 位字符串。编码后为 44 个字符，包含末尾的 <code>=</code>。
        </p>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="font-medium text-gray-800">在本地终端运行（需要 Python）</p>
          <button type="button" class="rounded border border-gray-300 bg-white px-2 py-1 text-primary hover:bg-gray-100" @click="copyExample(generationCommand, '生成命令')">
            复制生成命令
          </button>
        </div>
        <pre class="overflow-x-auto rounded border border-gray-200 bg-white p-2"><code>{{ generationCommand }}</code></pre>
        <p>复制只复制命令，不会运行命令，也不会生成或保存密钥。</p>
      </div>

      <div class="space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="font-medium text-gray-800">首次配置</p>
          <button type="button" class="rounded border border-gray-300 bg-white px-2 py-1 text-primary hover:bg-gray-100" @click="copyExample(firstSetupExample, '首次配置示例')">
            复制首次配置示例
          </button>
        </div>
        <p>运行一次命令，用输出替换占位符，再将完整 JSON 粘贴到密钥环输入框并保存。占位符本身不能保存为密钥。</p>
        <pre class="overflow-x-auto rounded border border-gray-200 bg-white p-2"><code>{{ firstSetupExample }}</code></pre>
      </div>

      <div class="space-y-2">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="font-medium text-gray-800">密钥轮换</p>
          <button type="button" class="rounded border border-gray-300 bg-white px-2 py-1 text-primary hover:bg-gray-100" @click="copyExample(rotationExample, '轮换示例')">
            复制轮换示例
          </button>
        </div>
        <p>
          重新运行命令生成新密钥，使用尚未使用的编号，并将 <code>active_key_id</code> 切换到新编号。
          例如已有 <code>cred-v1</code> 时，可提交以下配置：
        </p>
        <pre class="overflow-x-auto rounded border border-gray-200 bg-white p-2"><code>{{ rotationExample }}</code></pre>
        <p>
          保存时服务端会自动保留可读取的旧密钥，新写入使用新编号，历史密文仍使用旧编号。
          不需要读取旧密钥重新填写；不能用同一编号替换成另一把密钥，也不能删除历史密文仍需使用的旧密钥。
        </p>
      </div>

      <div class="space-y-1 border-t border-gray-200 pt-3">
        <p>JSON 顶层只包含 <code>active_key_id</code> 和 <code>keys</code>；<code>active_key_id</code> 必须对应 <code>keys</code> 中存在的编号。</p>
        <p>编号长度为 1–48 位，只能使用英文字母、数字、下划线和短横线。<code>keys</code> 中每个值都必须符合上述密钥格式。</p>
        <p>输入框留空时不会提交密钥环，不会清空已保存的值；没有其他修改时不会保存。已有密钥环未配置或无效时，不能只打开开关来启用结构化凭据。</p>
      </div>
      <p role="status" aria-live="polite" class="text-primary">{{ copyFeedback }}</p>
    </div>
  </details>
</template>
