/**
 * 真渲染两个组件，验证「进度小节默认展开、可收起」与「正文已展示时卡片不再重复」。
 *
 * 为什么不用文本断言：这次的需求就是观感（同一段进度在一屏里出现两次），
 * 光断言模板里有某个字符串，证明不了「默认是展开的」「传了 summaryRendered 就真的不渲染」。
 * 这里用与构建相同的 `@vue/compiler-sfc` 编译 SFC，再用 `vue/server-renderer` 渲染成 HTML。
 */
const fs = require('fs')
const path = require('path')
const Module = require('module')
const { createRequire } = require('module')

const ROOT = path.join(__dirname, '../..')
const FRONTEND = path.join(ROOT, 'frontend')
const frontendRequire = createRequire(path.join(FRONTEND, 'package.json'))
const esbuild = require(frontendRequire.resolve('esbuild'))
const { parse, compileScript } = frontendRequire('vue/compiler-sfc')
const { createSSRApp } = frontendRequire('vue')
const { renderToString } = frontendRequire('vue/server-renderer')

function loadSfc(relativePath) {
  const file = path.join(ROOT, relativePath)
  const source = fs.readFileSync(file, 'utf8')
  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) throw new Error(errors[0].message)
  // inlineTemplate：把 render 内联进 setup，省掉手工拼 render 选项
  const compiled = compileScript(descriptor, {
    id: path.basename(file),
    inlineTemplate: true,
  })
  const { outputFiles } = esbuild.buildSync({
    stdin: {
      contents: compiled.content,
      resolveDir: FRONTEND,
      loader: 'ts',
      sourcefile: file,
    },
    bundle: true,
    format: 'cjs',
    platform: 'node',
    target: 'node18',
    write: false,
    // 外置 vue：必须与下面 createSSRApp 用同一份实例，否则 SSR 上下文对不上
    external: ['vue'],
  })
  const mod = new Module(file, module)
  mod.filename = file
  mod.paths = Module._nodeModulePaths(path.dirname(file))
  mod._compile(outputFiles[0].text, file)
  return mod.exports.default
}

async function render(component, props) {
  return renderToString(createSSRApp(component, props))
}

const PROGRESS = '已完成：读取 cmdb-itam-query-guide 技能全文（13590 字符）。待办：写入技能。'
const QUESTION = '「设备不在机柜就在仓库，位置信息二选一」这条规则请确认要覆盖的范围：'

const payload = {
  question_id: 'q-probe',
  question: QUESTION,
  options: [
    { id: 'a', label: '完整写入' },
    { id: 'b', label: '只加坑位' },
  ],
  is_multi_select: false,
  allow_custom_input: false,
  context: PROGRESS,
  status: 'pending',
}

;(async () => {
  const Block = loadSfc('frontend/src/components/chat/UserQuestionBodyBlock.vue')
  const Card = loadSfc('frontend/src/components/UserQuestionCard.vue')

  const progressOpen = await render(Block, { label: '任务进度', tone: 'progress', text: PROGRESS })
  const progressCollapsed = await render(Block, {
    label: '任务进度',
    tone: 'progress',
    text: PROGRESS,
    collapsed: true,
  })
  const pendingBlock = await render(Block, { label: '待你确认', tone: 'pending', text: QUESTION })
  // 卡片：status pending 才是展开态（v-show 展开，内容参与布局）
  const cardWithSummary = await render(Card, { payload, summaryRendered: true })
  const cardPlain = await render(Card, { payload })

  const out = {
    block: {
      // 不传 collapsed 时必须能看到正文：这就是「默认展开」
      open_shows_text: progressOpen.includes(PROGRESS),
      open_marks_tone: progressOpen.includes('data-question-body-block="progress"'),
      open_has_tag: progressOpen.includes('任务进度'),
      collapsed_hides_text: !progressCollapsed.includes(PROGRESS),
      collapsed_keeps_tag: progressCollapsed.includes('任务进度'),
      pending_marks_tone: pendingBlock.includes('data-question-body-block="pending"'),
      pending_uses_amber: pendingBlock.includes('bg-amber-50/50'),
      aria_expanded_open: progressOpen.includes('aria-expanded="true"'),
      aria_expanded_collapsed: progressCollapsed.includes('aria-expanded="false"'),
    },
    card: {
      // summaryRendered：问题与进度都已由正文小节承载，卡片里不能再出现一遍
      summary_hides_question: !cardWithSummary.includes(QUESTION),
      summary_hides_progress: !cardWithSummary.includes(PROGRESS),
      summary_keeps_options: cardWithSummary.includes('完整写入') && cardWithSummary.includes('只加坑位'),
      summary_keeps_header: cardWithSummary.includes('需要你的补充'),
      // 没传时行为完全不变（老消息、调试页）
      plain_shows_question: cardPlain.includes(QUESTION),
      plain_shows_progress: cardPlain.includes(PROGRESS),
    },
  }

  process.stdout.write(JSON.stringify(out))
})().catch((error) => {
  console.error(error)
  process.exit(1)
})
