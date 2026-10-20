<script setup lang="ts">
/**
 * 请求签名接入说明。
 *
 * 这是给「业务方」看的，不是给平台开发者看的：所以先讲怎么验、再讲平台怎么算，
 * 最后给一组固定测试向量，让对方能在不接平台的情况下先自测。弹层里只出现公开的
 * 测试密钥，绝不读取当前工具的真实密钥。
 */
import Modal from '../Modal.vue'
import CopyButton from '../common/CopyButton.vue'
import {
  SIGNING_CANONICAL_EXAMPLE,
  SIGNING_CURL_EXAMPLE,
  SIGNING_HEADERS,
  SIGNING_PYTHON_EXAMPLE,
  SIGNING_TEST_VECTOR,
} from '../../utils/requestSigning'

defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const headerRows = [
  {
    name: 'X-Nanzi-Signature-Version',
    description: '协议版本，当前固定为 1。',
  },
  {
    name: 'X-Nanzi-Key-Id',
    description: '平台为这个工具生成的 Key-Id；业务方据此选择对应共享密钥。',
  },
  {
    name: 'X-Nanzi-Timestamp',
    description: 'Unix 秒级时间戳。建议业务方校验与本地时间相差不超过 5 分钟。',
  },
  {
    name: 'X-Nanzi-Nonce',
    description: '每次请求随机生成的 32 位十六进制串；可选做短期去重防重放。',
  },
  {
    name: 'X-Nanzi-Signature',
    description: '对下面 8 行签名串做 HMAC-SHA256，小写十六进制。',
  },
]

// 逐行对照固定向量：只看下面那段 8 行文本，业务方很难知道每一行对应什么。
const canonicalLineRows = [
  {
    line: '1',
    field: 'VERSION',
    description: '协议版本，当前固定为 1，与 X-Nanzi-Signature-Version 一致。',
    value: '1',
  },
  {
    line: '2',
    field: 'METHOD',
    description: 'HTTP 方法，统一转成大写。',
    value: SIGNING_TEST_VECTOR.method,
  },
  {
    line: '3',
    field: 'PATH',
    description: '实际发送的百分号编码 path，不含查询串，也不含域名。',
    value: SIGNING_TEST_VECTOR.path,
  },
  {
    line: '4',
    field: 'CANONICAL_QUERY',
    description: '按第 3 节规则排序、编码后的查询串；没有查询参数时这一行留空。',
    value: SIGNING_TEST_VECTOR.query,
  },
  {
    line: '5',
    field: 'SHA256_HEX_RAW_BODY',
    description: `原始请求体字节的 SHA-256，小写十六进制；本次示例 body 是 ${SIGNING_TEST_VECTOR.body}，先算哈希再填入，不要解析 JSON 后重新序列化。`,
    value: SIGNING_TEST_VECTOR.bodyHash,
  },
  {
    line: '6',
    field: 'TIMESTAMP',
    description: 'Unix 秒级时间戳，与 X-Nanzi-Timestamp Header 完全一致。',
    value: SIGNING_TEST_VECTOR.timestamp,
  },
  {
    line: '7',
    field: 'NONCE',
    description: '本次请求的随机 32 位十六进制串，与 X-Nanzi-Nonce Header 完全一致。',
    value: SIGNING_TEST_VECTOR.nonce,
  },
  {
    line: '8',
    field: 'KEY_ID',
    description: '平台分配的 Key-Id，与 X-Nanzi-Key-Id Header 完全一致。',
    value: SIGNING_TEST_VECTOR.keyId,
  },
]
</script>

<template>
  <Modal
    title="请求签名接入说明"
    :show="show"
    size="max-w-3xl"
    :z-index="12000"
    @close="emit('close')"
  >
    <div class="space-y-5 text-sm leading-relaxed text-gray-700">
      <section class="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
        <h4 class="font-bold text-indigo-900">它解决什么，不解决什么</h4>
        <p class="mt-1 text-xs leading-relaxed text-indigo-800">
          开启后，平台对<strong>最终实际发出</strong>的请求计算 HMAC-SHA256。业务方用同一个共享密钥验签，
          可以确认请求确实来自平台、路径/查询参数/请求体没有被中途改动。
        </p>
        <p class="mt-2 text-xs leading-relaxed text-indigo-800">
          它<strong>不能</strong>阻止模型或用户把业务参数填错，也不能替代 HTTPS、权限校验和业务规则；
          重放防护需要业务方结合 Timestamp 与 Nonce 自行判断。密钥泄露后应立即轮换。
        </p>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">1. 平台会带这 5 个 Header</h4>
        <div class="mt-2 overflow-hidden rounded-lg border border-gray-200">
          <table class="min-w-full divide-y divide-gray-100 text-xs">
            <thead class="bg-gray-50 text-left text-gray-500">
              <tr>
                <th class="px-3 py-2 font-medium">Header</th>
                <th class="px-3 py-2 font-medium">含义</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 bg-white">
              <tr v-for="row in headerRows" :key="row.name">
                <td class="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-gray-800">{{ row.name }}</td>
                <td class="px-3 py-2 text-gray-600">{{ row.description }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-2 text-xs text-gray-500">
          Header 名称大小写不敏感；下面是固定顺序，业务方按名字取即可：
          <span class="font-mono">{{ SIGNING_HEADERS.join('、') }}</span>。
        </p>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">2. 业务方验签步骤</h4>
        <ol class="mt-2 list-decimal space-y-1.5 pl-5 text-xs leading-relaxed text-gray-600">
          <li>读取 5 个 <code class="font-mono">X-Nanzi-*</code> Header；缺少任意一个直接拒绝。</li>
          <li>用 <code class="font-mono">X-Nanzi-Key-Id</code> 找到对应共享密钥（密钥由平台在工具编辑页生成后线下交付）。</li>
          <li>校验 <code class="font-mono">X-Nanzi-Timestamp</code> 在允许的时间窗内；需要防重放时再用 Nonce 做短期去重。</li>
          <li>按第 3 节规则拼出 8 行签名串；查询参数要排序并保持重复 key。</li>
          <li>对原始请求体字节做 SHA-256，不要先解析 JSON 再重新序列化。</li>
          <li>用 HMAC-SHA256 计算签名，与 <code class="font-mono">X-Nanzi-Signature</code> 做常量时间比较。</li>
        </ol>
      </section>

      <section>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="font-bold text-gray-900">3. 8 行签名串逐行说明（UTF-8，无末尾换行）</h4>
          <CopyButton :text="SIGNING_CANONICAL_EXAMPLE" label="签名串示例" />
        </div>
        <pre class="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ SIGNING_CANONICAL_EXAMPLE }}</pre>
        <p class="mt-3 text-xs text-gray-600">
          下面按行号逐行解释上面的示例。<strong>行号只是对照说明，不参与签名</strong>；拼接时不要写行号，也不要在末尾添加换行。
        </p>
        <div class="mt-2 overflow-hidden rounded-lg border border-gray-200 bg-white">
          <div class="hidden grid-cols-[2rem_9rem_1fr] gap-x-2 border-b border-gray-100 bg-gray-50 px-3 py-1.5 text-[10px] font-medium text-gray-400 sm:grid">
            <span>行</span>
            <span>字段</span>
            <span>含义与示例值</span>
          </div>
          <div
            v-for="row in canonicalLineRows"
            :key="row.line"
            class="grid grid-cols-[2rem_1fr] gap-x-2 gap-y-1 border-b border-gray-100 px-3 py-2 text-xs last:border-0 sm:grid-cols-[2rem_9rem_1fr]"
          >
            <span class="font-mono font-bold text-gray-400">{{ row.line }}</span>
            <span class="font-mono font-semibold text-gray-800">{{ row.field }}</span>
            <div class="col-span-2 min-w-0 sm:col-span-1">
              <p class="leading-relaxed text-gray-600">{{ row.description }}</p>
              <code class="mt-0.5 block break-all font-mono text-[11px] text-indigo-700">{{ row.value }}</code>
            </div>
          </div>
        </div>
        <ul class="mt-2 list-disc space-y-1 pl-5 text-xs text-gray-600">
          <li><code class="font-mono">CANONICAL_QUERY</code>：按解码后的 key/value 排序；保留重复 key 与空值；空格编码为 <code class="font-mono">%20</code>，不要用 <code class="font-mono">+</code>。</li>
          <li><code class="font-mono">SHA256_HEX_RAW_BODY</code>：空体固定为 <code class="font-mono">e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855</code>。</li>
          <li>第 4 行查询串为空时，保留一个空行，行数仍是 8。</li>
        </ul>
      </section>

      <section>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="font-bold text-gray-900">4. Python 验签示例</h4>
          <CopyButton :text="SIGNING_PYTHON_EXAMPLE" label="Python 示例" />
        </div>
        <pre class="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ SIGNING_PYTHON_EXAMPLE }}</pre>
        <p class="mt-2 text-xs text-gray-500">
          示例已按第 3 节实现 <code class="font-mono">canonical_query</code>；不同框架取原始 path / body
          的方式不同，务必以框架实际发出的字节为准。
        </p>
      </section>

      <section>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="font-bold text-gray-900">5. 固定测试向量（可直接自测）</h4>
          <CopyButton :text="SIGNING_CURL_EXAMPLE" label="测试 curl" />
        </div>
        <div class="mt-2 grid grid-cols-1 gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-[11px] text-gray-700 sm:grid-cols-2">
          <div><span class="text-gray-400">secret：</span>{{ SIGNING_TEST_VECTOR.secret }}</div>
          <div><span class="text-gray-400">key_id：</span>{{ SIGNING_TEST_VECTOR.keyId }}</div>
          <div><span class="text-gray-400">timestamp：</span>{{ SIGNING_TEST_VECTOR.timestamp }}</div>
          <div><span class="text-gray-400">nonce：</span>{{ SIGNING_TEST_VECTOR.nonce }}</div>
          <div><span class="text-gray-400">method/path：</span>{{ SIGNING_TEST_VECTOR.method }} {{ SIGNING_TEST_VECTOR.path }}</div>
          <div><span class="text-gray-400">query：</span>{{ SIGNING_TEST_VECTOR.query }}</div>
          <div class="sm:col-span-2"><span class="text-gray-400">body：</span>{{ SIGNING_TEST_VECTOR.body }}</div>
          <div class="sm:col-span-2 break-all"><span class="text-gray-400">body sha256：</span>{{ SIGNING_TEST_VECTOR.bodyHash }}</div>
          <div class="sm:col-span-2 break-all text-emerald-700"><span class="text-gray-400">signature：</span>{{ SIGNING_TEST_VECTOR.signature }}</div>
        </div>
        <p class="mt-2 text-xs text-gray-500">
          这组值是公开的固定测试向量，不是任何真实工具的密钥；业务方可以用它先跑通自己的验签代码。
        </p>
      </section>

      <section class="rounded-xl border border-amber-200 bg-amber-50/70 p-4">
        <h4 class="font-bold text-amber-900">6. 上线前请注意</h4>
        <ul class="mt-1 list-disc space-y-1 pl-5 text-xs leading-relaxed text-amber-800">
          <li>平台在工具编辑页生成密钥后<strong>只显示一次</strong>，请立即交付业务方；服务端不会回显明文。</li>
          <li>轮换时同时更换 Key-Id 和密钥，业务方保留旧 Key-Id 一段时间做灰度。</li>
          <li>关闭开关只停止签名，不删除已保存密钥；重新开启会继续使用原密钥。</li>
          <li>平台签名覆盖最终请求，业务方不要先改写请求体再验签。</li>
        </ul>
      </section>
    </div>

    <template #footer>
      <div class="flex justify-end">
        <button
          type="button"
          class="rounded-md bg-primary px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-dark"
          @click="emit('close')"
        >我知道了</button>
      </div>
    </template>
  </Modal>
</template>
