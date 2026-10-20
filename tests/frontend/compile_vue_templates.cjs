/**
 * 编译所有 Vue 单文件组件的模板，找出语法级错误。
 *
 * 为什么需要它：`vue-tsc --noEmit` 只做类型检查，不校验模板属性重复这类语法问题；
 * 这类错误要等 `vite build` 才会暴露（例如同一个标签上写了两个 `v-if`）。
 * 在 pytest 里提前跑一遍，构建前就能拦住。
 *
 * 已知可忽略项：AgentDebug.vue 模板里内联的 <script> 会被 compiler 提示
 * "Tags with side effect ... are ignored"，这是提示而非语法错误。
 */

const fs = require('fs')
const path = require('path')
const { createRequire } = require('module')

const ROOT = path.join(__dirname, '../..')
// 脚本在 tests/ 下，依赖装在 frontend/node_modules，必须显式按 frontend 解析
const frontendRequire = createRequire(path.join(ROOT, 'frontend/package.json'))
const { parse, compileTemplate } = frontendRequire('vue/compiler-sfc')

const IGNORED = [/Tags with side effect/]

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!['node_modules', 'dist'].includes(entry.name)) walk(full, out)
    } else if (entry.name.endsWith('.vue')) {
      out.push(full)
    }
  }
  return out
}

const problems = []
const files = walk(path.join(ROOT, 'frontend/src'))

for (const file of files) {
  const source = fs.readFileSync(file, 'utf8')
  const relative = path.relative(ROOT, file)

  const { descriptor, errors } = parse(source, { filename: file })
  if (errors.length) {
    problems.push(`${relative}: ${errors[0].message}`)
    continue
  }
  if (!descriptor.template) continue

  const result = compileTemplate({
    source: descriptor.template.content,
    filename: file,
    id: file,
  })
  for (const error of result.errors) {
    const message = String(error.message || error).split('\n')[0]
    if (IGNORED.some((pattern) => pattern.test(message))) continue
    problems.push(`${relative}: ${message}`)
  }
}

process.stdout.write(JSON.stringify({ total: files.length, problems }))
process.exit(problems.length ? 1 : 0)
