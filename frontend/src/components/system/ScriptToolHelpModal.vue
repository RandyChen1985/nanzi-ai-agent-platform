<script setup lang="ts">
/**
 * 脚本工具使用指南。
 *
 * 这是给「业务方 / 管理员」看的，不是给平台开发者看的：所以先给一段能直接复制的
 * 最小模板，再把 stdin/stdout/退出码这些必须遵守的约定摊成表格，最后才讲文件、
 * 依赖这些容易踩坑的地方。
 *
 * 沙箱提示不在这里写死：不同部署（docker / k8s / e2b / ssh / local）下脚本跑在
 * 哪里并不一样，文案由调用方按后端返回的 sandbox_policy 生成后用 ``sandboxNotice``
 * 传进来，本组件只负责原样渲染——绝不自己编一句看起来更安全的话。
 */
import Modal from '../Modal.vue'
import CopyButton from '../common/CopyButton.vue'

defineProps<{
  show: boolean
  /** 沙箱位置提示（按部署策略生成）；不传则不显示提示条 */
  sandboxNotice?: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

/** 最小可用模板：读参数、校验、输出、报错四件事各占一行，复制后改中间那段就行。 */
const MINIMAL_TEMPLATE = `"""最小可用脚本：stdin 收参数，stdout 出结果。"""
import json
import sys


def main() -> None:
    # 1) 参数：一个 JSON 对象，键就是你在「参数」里声明的参数名
    params = json.load(sys.stdin)
    text = str(params.get("text", "")).strip()

    # 2) 校验不过就退出码非 0，并在 stderr 说清原因（会原样回给模型）
    if not text:
        print("参数 text 不能为空", file=sys.stderr)
        sys.exit(2)

    # 3) 结果：用 JSON 输出，ensure_ascii=False 中文才不会变成 \\uXXXX
    result = {"length": len(text), "upper": text.upper()}
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()`

/** 一次调用的约定：左边是平台承诺，右边是脚本里对应的写法。 */
const contractRows = [
  {
    item: '输入（stdin）',
    rule: '一个 JSON 对象，键就是你在「参数」里声明的参数名；没有参数时是空对象 {}。',
    code: 'params = json.load(sys.stdin)',
  },
  {
    item: '输出（stdout）',
    rule: '文本或 JSON。能解析成 JSON 就把结构化结果交给模型，否则原样当文本返回。想给模型稳定字段就输出 JSON。',
    code: 'json.dump(result, sys.stdout, ensure_ascii=False)',
  },
  {
    item: '成功 / 失败',
    rule: '退出码 0 表示成功；非 0 一律视为失败，模型会收到「脚本执行失败（退出码 N）」；stderr 与已捕获的 stdout 会一并返回用于排查，但不会当作成功结果。',
    code: 'sys.exit(2)',
  },
  {
    item: '错误详情（stderr）',
    rule: 'stderr 会作为错误详情回给模型，请只用一两句话写「为什么不成功、下一步该补什么」，不要打印整段 traceback 噪音。',
    code: 'print("缺少参数 xxx", file=sys.stderr)',
  },
  {
    item: '单次输出上限',
    rule: 'stdout 最多 80KB，超出会被截断并标注。大块内容（base64、长表格）请写进工作区文件，用 artifacts 交付。',
    code: '见下方「文件读写」',
  },
  {
    item: '超时',
    rule: '在向导第 4 步设置的秒数；留空则跟随平台的「工具调用超时」。沙箱内单条命令另有 600 秒硬上限，超时后进程会被终止，脚本拿不到收尾机会。',
    code: '—',
  },
]

/** 产物登记示例：平台只认 stdout 里这个键，其它写法都不会生成下载链接。 */
const ARTIFACT_SAMPLE = `{
  "summary": "共 12 条记录",
  "artifacts": ["reports/2026-10-summary.xlsx"]
}`

/** 本地自测：先把脚本在终端跑通，再搬到平台上，能省掉一大半排查时间。 */
const SELF_TEST_COMMAND = `# 用一份和真实调用一样的参数，在本地先把脚本跑通
echo '{"text": "hello 世界"}' | python tool.py`

const dependencyRows = [
  {
    write: 'pypdf>=4.0',
    meaning: '写法与 requirements.txt 完全一致，一行一个包；版本号建议写上，避免哪天上游升级把脚本跑挂。',
  },
  {
    write: 'python-docx',
    meaning: '不需要版本约束时可以只写包名；平台按 pip 规则解析。',
  },
  {
    write: 'openpyxl==3.1.5',
    meaning: '需要固定版本时用 ==；不要写 pip install 之类命令，这里只填包名。',
  },
]

/** 内置脚本索引：与平台内置清单一一对应，页面只做索引，不复制它们的源码。 */
const builtinTools = [
  {
    name: 'file_to_base64',
    displayName: '文件转 base64',
    usage: '把工作区里的图片、PDF 等文件读成 base64 文本，或直接输出 data-uri。',
    group: '通用文件',
  },
  {
    name: 'base64_to_file',
    displayName: 'base64 转文件',
    usage: '把接口返回的 base64 还原成文件写进工作区，并登记成可下载附件。',
    group: '通用文件',
  },
  {
    name: 'file_digest',
    displayName: '计算文件/文本摘要',
    usage: '算 md5 / sha1 / sha256，用于校验传输完整性、比对版本或生成对账指纹。',
    group: '通用文件',
  },
  {
    name: 'text_regex_extract',
    displayName: '正则批量提取',
    usage: '用正则从日志、邮件、报表里批量抽取订单号、金额、IP 等字段。',
    group: '通用文本',
  },
  {
    name: 'text_redact',
    displayName: '敏感信息脱敏',
    usage: '手机号、身份证号、邮箱、银行卡号脱敏，输出可以对外发送的文本。',
    group: '通用文本',
  },
]
</script>

<template>
  <Modal
    title="脚本工具使用指南"
    :show="show"
    size="max-w-3xl"
    :z-index="12000"
    @close="emit('close')"
  >
    <div class="space-y-5 text-sm leading-relaxed text-gray-700">
      <!-- 沙箱位置：不同部署不一样，所以原样渲染调用方给的提示，不在这里写死 -->
      <div
        v-if="sandboxNotice"
        class="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/80 px-3 py-2.5"
      >
        <span class="mt-0.5 shrink-0 rounded bg-amber-200/70 px-1.5 py-0.5 text-[10px] font-bold text-amber-900">运行位置</span>
        <p class="text-xs leading-relaxed text-amber-800">{{ sandboxNotice }}</p>
      </div>

      <section class="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
        <h4 class="font-bold text-indigo-900">它是什么</h4>
        <p class="mt-1 text-xs leading-relaxed text-indigo-800">
          把一段 Python 脚本登记成工具后，模型在对话里就能调用它：你在「参数」里声明的字段会被模型自动填好，
          脚本在<strong>顶部「运行位置」提示所指的工作区</strong>里执行，输出直接回到对话里。
          它适合「文件转 base64」「算哈希」「文本脱敏」这类处理；不要假设它一定访问不到外部网络或平台内网，
          需要联网/访问外部系统时请改用 API 工具。
        </p>
        <p class="mt-2 text-xs leading-relaxed text-indigo-800">
          每条脚本只是<strong>一次短暂的命令执行</strong>：没有常驻进程，不能等待人工输入，也不能跨调用保存内存里的变量；
          需要留存的东西（文件、中间结果）一律写进工作区。
        </p>
      </section>

      <section>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h4 class="font-bold text-gray-900">1. 最小可用模板（可直接复制）</h4>
          <CopyButton :text="MINIMAL_TEMPLATE" label="最小模板" />
        </div>
        <pre class="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ MINIMAL_TEMPLATE }}</pre>
        <ul class="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-gray-600">
          <li>复制到「代码」步骤后，把 <code class="font-mono">text</code> 换成你参数表里的字段即可。</li>
          <li>参数一律用 <code class="font-mono">params.get("名字")</code> 取，不要假设模型一定传；缺字段时自己给默认值或明确报错。</li>
          <li>输出保持<strong>一段干净的 JSON</strong>：不要把日志和结果混着 print，日志请写 stderr。</li>
          <li>退出码 <code class="font-mono">sys.exit(2)</code> 表示「参数不对」，脚本内部异常导致的 1 也会被识别成失败，但错误详情不如自己写清楚。</li>
        </ul>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">2. 运行契约（模型怎么调、结果怎么回）</h4>
        <div class="mt-2 overflow-hidden rounded-lg border border-gray-200">
          <table class="min-w-full divide-y divide-gray-100 text-xs">
            <thead class="bg-gray-50 text-left text-gray-500">
              <tr>
                <th class="px-3 py-2 font-medium whitespace-nowrap">项目</th>
                <th class="px-3 py-2 font-medium">约定</th>
                <th class="hidden px-3 py-2 font-medium sm:table-cell">脚本里怎么写</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 bg-white">
              <tr v-for="row in contractRows" :key="row.item">
                <td class="whitespace-nowrap px-3 py-2 align-top font-medium text-gray-800">{{ row.item }}</td>
                <td class="px-3 py-2 align-top text-gray-600">
                  {{ row.rule }}
                  <code class="mt-1 block break-all font-mono text-[11px] text-indigo-700 sm:hidden">{{ row.code }}</code>
                </td>
                <td class="hidden px-3 py-2 align-top sm:table-cell">
                  <code class="block break-all font-mono text-[11px] text-indigo-700">{{ row.code }}</code>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-2 text-xs leading-relaxed text-gray-500">
          三个数字要记住：<strong>退出码 0 = 成功</strong>、<strong>非 0 = 失败</strong>、<strong>单次输出 80KB 封顶</strong>。
        </p>

        <div class="mt-3 rounded-lg border border-gray-200 bg-gray-50/60 p-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="text-xs font-semibold text-gray-800">先在终端自测一遍，再搬到平台上</p>
            <CopyButton :text="SELF_TEST_COMMAND" label="自测命令" />
          </div>
          <pre class="mt-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ SELF_TEST_COMMAND }}</pre>
          <p class="mt-1.5 text-[11px] leading-relaxed text-gray-500">
            平台执行的就是「把参数 JSON 从 stdin 喂进去」这一件事，所以本地跑通、输出是合法 JSON，平台上基本不会出问题。
          </p>
        </div>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">3. 文件读写：只在当前会话工作区里转</h4>
        <ul class="mt-2 list-disc space-y-1.5 pl-5 text-xs leading-relaxed text-gray-600">
          <li>
            脚本应当只读写<strong>当前工作区</strong>（沙箱内即 <code class="font-mono">/workspace</code>）。
            用户上传的文件、上一个工具生成的产物都在这里。实际可见范围取决于部署的沙箱策略：
            本地策略下脚本以平台后端进程权限运行，Docker 挂载的是用户工作区根目录——
            不要把「只能访问工作区」当作安全边界。
          </li>
          <li>
            路径一律写<strong>相对路径</strong>，例如 <code class="font-mono">uploads/a.pdf</code>、<code class="font-mono">reports/out.xlsx</code>；
            不要写 <code class="font-mono">/tmp</code>、<code class="font-mono">/Users/...</code> 这类绝对路径，换台机器就废了。
          </li>
          <li>
            要交付给用户下载的文件：<strong>写进工作区</strong>，并在 stdout 的 JSON 里给出
            <code class="font-mono">artifacts</code> 数组（相对路径）。平台会把它登记成可下载文件，附在这次调用的结果里。
          </li>
          <li>
            只写 <code class="font-mono">artifacts</code> 里列出的文件才会被登记；写进工作区但没列出的，用户只能在文件区自己找。
          </li>
          <li>临时文件请放在工作区内并在结束时清理，不要指望下次调用还在——不同会话的工作区互不相通。</li>
        </ul>
        <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p class="text-xs font-semibold text-gray-800">产物登记示例（stdout 输出这段 JSON）</p>
          <CopyButton :text="ARTIFACT_SAMPLE" label="产物示例" />
        </div>
        <pre class="mt-1.5 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[11px] leading-relaxed text-slate-100">{{ ARTIFACT_SAMPLE }}</pre>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">4. 依赖声明：写在「依赖」里，别自己 pip install</h4>
        <p class="mt-1 text-xs leading-relaxed text-gray-600">
          第三方包在向导第 4 步「依赖」里一行一个包名填写，写法和 <code class="font-mono">requirements.txt</code> 完全一致。
          首次调用该工具时，平台会把它们装到<strong>当前会话工作区的 <code class="font-mono">.pylibs</code></strong>，
          并把该目录注入 <code class="font-mono">PYTHONPATH</code>，脚本里直接 <code class="font-mono">import</code> 就能用。
        </p>
        <div class="mt-2 overflow-hidden rounded-lg border border-gray-200">
          <table class="min-w-full divide-y divide-gray-100 text-xs">
            <thead class="bg-gray-50 text-left text-gray-500">
              <tr>
                <th class="px-3 py-2 font-medium whitespace-nowrap">这样写</th>
                <th class="px-3 py-2 font-medium">说明</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 bg-white">
              <tr v-for="row in dependencyRows" :key="row.write">
                <td class="whitespace-nowrap px-3 py-2 align-top font-mono text-[11px] text-gray-800">{{ row.write }}</td>
                <td class="px-3 py-2 text-gray-600">{{ row.meaning }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul class="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-gray-600">
          <li>只用标准库（<code class="font-mono">json</code>、<code class="font-mono">hashlib</code>、<code class="font-mono">re</code>、<code class="font-mono">base64</code>…）就留空，又快又稳。</li>
          <li>不要写 <code class="font-mono">pip install</code> 命令，也不要在脚本里 <code class="font-mono">subprocess</code> 调 pip：装包不在脚本的执行预算里，而且沙箱内通常没有网络。</li>
          <li>首次调用会多等一会儿（装依赖有独立的时间预算，不占工具超时）；之后命中缓存，直接复用。</li>
          <li>装不上时会明确告诉你「缺哪个包 + 原始报错」，照着把包名或版本改对再试。</li>
        </ul>
      </section>

      <section>
        <h4 class="font-bold text-gray-900">5. 平台内置的 5 个脚本</h4>
        <p class="mt-1 text-xs leading-relaxed text-gray-600">
          下面这些开箱即用，已经注册好、可以直接挂给智能体。它们<strong>不能被直接修改</strong>：
          想调整行为就在列表里点「复制为新工具」，得到一份可编辑的副本。
        </p>
        <div class="mt-2 overflow-hidden rounded-lg border border-gray-200">
          <table class="min-w-full divide-y divide-gray-100 text-xs">
            <thead class="bg-gray-50 text-left text-gray-500">
              <tr>
                <th class="px-3 py-2 font-medium">名称</th>
                <th class="px-3 py-2 font-medium">一句话用途</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 bg-white">
              <tr v-for="tool in builtinTools" :key="tool.name">
                <td class="px-3 py-2 align-top">
                  <span class="block whitespace-nowrap font-mono text-[11px] font-semibold text-gray-800">{{ tool.name }}</span>
                  <span class="mt-0.5 block text-[11px] text-gray-500">{{ tool.displayName }} · {{ tool.group }}</span>
                </td>
                <td class="px-3 py-2 align-top text-gray-600">{{ tool.usage }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-[11px] leading-relaxed text-gray-500">
          这 5 个都只依赖 Python 标准库，不需要装任何包，可以拿来当写法参考。
          点「复制为新工具」后，<strong>记得把名称改掉</strong>（复制出来的名字会自动加 <code class="font-mono">_copy</code> 后缀），
          否则模型和智能体配置里会出现两个几乎一样的工具，很难分辨该用哪个。
        </p>
      </section>

      <section class="rounded-xl border border-amber-200 bg-amber-50/70 p-4">
        <h4 class="font-bold text-amber-900">6. 上线前的几条提醒</h4>
        <ul class="mt-1 list-disc space-y-1 pl-5 text-xs leading-relaxed text-amber-800">
          <li>脚本以<strong>平台服务身份</strong>在会话工作区里运行，写权限与 API 工具同级；只登记可信的脚本，不要执行从网上下载、来源不明的代码。</li>
          <li>描述里写清「什么场景该用它」，模型是按场景挑工具的；只写「处理文本」很容易被误用。</li>
          <li>保存后还要到智能体配置的「工具能力」里把它勾上，才会真正生效。</li>
          <li>停用只影响新调用，不会删除脚本；误删无法恢复，重要脚本请先复制一份。</li>
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
