/**
 * 「让 AI 生成工具 JSON」的提示词。
 *
 * 为什么放常量里而不是内联在组件中：
 * 1. 提示词里写的是**平台真实运行时契约**（stdin/stdout、artifacts、工作区路径），
 *    它和 ``script_tool_runtime.py`` 强耦合，值得单独成文件、单独测；
 * 2. 以后接「工具市场」「AI 生成工具」其他地方也能复用同一份。
 *
 * 契约来源：``app/services/ai/tools/script_tool_runtime.py``
 *  - 参数从 stdin 读 JSON，结果写 stdout（可被解析成 JSON 就结构化返回）
 *  - 输出 JSON 里的 ``artifacts`` 数组会被提取成可下载产物
 *  - cwd 是当前会话工作区，越界路径必须由脚本自己拒绝
 *  - stdout 有约 80KB 的截断上限，大内容不能塞 stdout
 */

/**
 * 需求占位符：直接给一段**填好的**示例，而不是「请输入需求」这种空壳。
 *
 * 用户最常见的卡点是不知道需求该写到什么颗粒度 —— 写「做个 Excel 工具」生成的
 * 脚本往往缺参数、缺边界。这里用一个小而完整的例子示范四个要素：
 * 功能、输入参数、输出、特殊要求。
 */
export const SCRIPT_PROMPT_PLACEHOLDER = [
  '【下面这段是示例，请整体替换成你的需求】',
  '',
  '功能：把一段文本按每行最多 N 个字符重排，存成 .txt 文件供用户下载。',
  '输入参数：',
  '- text（字符串，必填）：要重排的原文',
  '- width（整数，可选，默认 80）：每行最大字符数，允许 20-200',
  '- filename（字符串，可选，默认 formatted.txt）：输出文件名',
  '输出：文件写好后返回 {"path": filename, "size": 字节数, "artifacts": [filename]}。',
  '特殊要求：纯标准库；原文超过 500KB 直接报错不截断；width 越界、text 为空都要给中文错误。',
].join('\n')

export interface ScriptPromptExample {
  label: string
  requirement: string
}

/** 示例需求：写得「像验收标准」而不是「像标题」，AI 生成的脚本才不容易跑偏 */
export const SCRIPT_PROMPT_EXAMPLES: ScriptPromptExample[] = [
  {
    label: 'JSON 转 Excel',
    requirement: [
      '输入一段 JSON 数组文本（每项是「字段名 → 基础类型值」的对象），输出一个 .xlsx 文件。',
      '参数：data（必填，JSON 字符串）、filename（默认 data.xlsx）、sheet_name（默认 Sheet1）、overwrite（默认 false）。',
      '要求：表头取所有对象键的并集，缺失单元格留空；文件写到工作区并在 artifacts 里登记；',
      '同名文件已存在且 overwrite 不为 true 时报错退出，不要静默覆盖。',
    ].join('\n'),
  },
  {
    label: 'CSV 分组统计',
    requirement: [
      '输入一段 CSV 文本，按指定列分组做计数与求和，结果用 JSON 输出。',
      '参数：csv（必填，CSV 文本）、group_by（必填，列名）、sum_column（可选，数值列名）、delimiter（默认英文逗号）。',
      '要求：首行是表头；输出 {groups: [{key, count, sum}], total_rows}；',
      '列名不存在时给出明确的中文错误；纯标准库实现，不引入 pandas。',
    ].join('\n'),
  },
  {
    label: '生成 HTML 报告',
    requirement: [
      '把一组指标数据渲染成一个单文件 HTML 报告并落盘，供用户下载。',
      '参数：title（必填）、metrics（必填，[{name, value, unit}] 数组）、filename（默认 report.html）。',
      '要求：HTML 里做基础转义，不能把用户输入当标签渲染；样式内联、不依赖外部 CDN；',
      '文件写到工作区并在 artifacts 登记；输出 JSON 里回传 {"path": "...", "size": 字节数, "artifacts": ["..."]}。',
    ].join('\n'),
  },
]

const PROMPT_HEAD = [
  '你是一名熟悉 Python 的 AI 工具工程师。请根据下面的【需求】，生成一个 NanZi 智能体平台',
  '可以直接导入的「脚本工具」JSON 包。导入后它会作为智能体的工具被调用。',
  '',
  '## 输出要求',
  '1. 只输出一个 ```json 代码块，前后不要任何解释、标题或第二个代码块。',
  '2. 顶层结构固定为：',
  '{',
  '  "schema": "nanzi.tool.bundle",',
  '  "schema_version": 1,',
  '  "tools": [ { 脚本工具条目 } ]',
  '}',
  '3. 需求包含多个工具时，tools 数组放多个条目；否则只放一个。',
  '',
  '## 脚本工具条目（kind 固定为 "script"）',
  '- name：必填。小写字母开头，仅含小写字母/数字/下划线，长度 3-64，例如 csv_to_excel。不能与平台已有工具重名。',
  '- code：必填。完整可运行的 Python 3 源码字符串，换行写成 \\n，最长 200KB。',
  '- display_name：中文展示名。',
  '- description：一句话说明用途与限制，写清楚输入、输出和边界。',
  '- language：固定 "python"（平台目前只支持 Python）。',
  '- parameter_schema：JSON Schema 对象（type=object），声明每个参数的类型、说明与 required。',
  '- requirements：第三方依赖数组，如 ["openpyxl>=3.1"]；只用标准库时写 []。',
  '- timeout_seconds：1-3600 的整数，或 null（跟随平台默认）。耗时任务要给足时间。',
  '- group_name：业务分组，如 "通用文件"、"数据处理"。',
  '- is_active：true。',
  '不要输出 id、source、is_overridden、created_at、updated_at：这些字段平台会生成或忽略。',
  '',
  '## 脚本运行时契约（必须遵守，否则导入后会跑不通）',
  '1. 参数从 stdin 读入：脚本启动后 json.load(sys.stdin)。不要用 input()，不要把用户数据写死在代码里。',
  '2. 结果写到 stdout：输出一个 JSON 对象（json.dump(..., sys.stdout, ensure_ascii=False)）；过程日志写 stderr。',
  '3. 失败要显式失败：中文错误写 stderr，并以非 0 退出码退出（sys.exit(2)）。不要 print 一句话却返回 0。',
  '4. 需要用户下载的文件：写入当前工作目录（Path.cwd()，即会话工作区），路径不得越出工作区；',
  '   并在输出 JSON 的 artifacts 数组里登记相对路径，例如',
  '   {"path": "report.xlsx", "size": 10240, "artifacts": ["report.xlsx"]}。',
  '5. 输出体积约 80KB 上限：不要把大文件内容塞进 stdout。二进制结果落盘并登记 artifacts；',
  '   需要回传给模型的长文本要截断，并在结果里说明已截断。',
  '6. 只依赖标准库时 requirements 留空；需要 openpyxl / pypdf 等库时写进 requirements，',
  '   平台会在执行前装到隔离目录并配好 PYTHONPATH。',
  '7. 不要联网，不要读取平台环境变量或数据库里的密钥；需要外部凭据时把它设计成参数。',
  '8. 参数缺失或格式错误时，给出能指导用户修正的中文错误信息。',
  '',
  '## 代码风格',
  '- 单文件、可直接运行，带 shebang 与一句话模块说明。',
  '- 用 fail(message, code=2) 之类的小函数统一报错。',
  '- 标准库十行能做完的事不要引入 pandas 之类重依赖。',
  '- 不要在模块顶层做 I/O，用 if __name__ == "__main__": main()。',
]

/**
 * 提示词里的「参考示例」。
 *
 * 用对象 + JSON.stringify 生成，而不是手写一段 JSON 文本：手写要自己处理 code 里的
 * 换行、引号和反斜杠，改一行就可能不是合法 JSON；模板字符串写代码、序列化交给运行时，
 * 更不容易出错。
 *
 * 这份示例不是装饰：它是「AI 照着写 → 导入 → 真的能跑」的样板，``code`` 会用真实
 * stdin 跑一遍验证（见 tests/api/portal/test_tool_bundle_contract.py）。
 */
const EXAMPLE_SCRIPT_TOOL = {
  kind: 'script',
  name: 'text_to_file',
  display_name: '文本写文件',
  description: '把一段文本写入工作区文件并登记为可下载产物；同名文件需显式覆盖。',
  language: 'python',
  code: `#!/usr/bin/env python3
"""text_to_file — 把一段文本写成工作区文件，并登记为可下载产物。"""

import json
import sys
from pathlib import Path


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def main():
    try:
        params = json.load(sys.stdin)
    except ValueError as exc:
        fail("参数不是合法 JSON：%s" % exc)
    if not isinstance(params, dict):
        fail("参数必须是 JSON 对象")

    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        fail("缺少必填参数 text")

    filename = str(params.get("filename") or "output.txt").strip()
    if "/" in filename or "\\\\" in filename or filename in (".", ".."):
        fail("filename 只能是文件名，不能带目录")

    root = Path.cwd().resolve()
    target = (root / filename).resolve()
    if target != root and root not in target.parents:
        fail("路径越界：只能写入当前会话工作区")
    if target.exists() and params.get("overwrite") is not True:
        fail("文件已存在：%s；如需覆盖请传 overwrite=true" % filename)

    target.write_text(text, encoding="utf-8")
    json.dump(
        {"path": filename, "size": target.stat().st_size, "artifacts": [filename]},
        sys.stdout,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    main()
`,
  parameter_schema: {
    type: 'object',
    properties: {
      text: { type: 'string', description: '要写入的文本内容' },
      filename: { type: 'string', description: '输出文件名（不含目录），默认 output.txt' },
      overwrite: { type: 'boolean', description: '同名文件是否覆盖', default: false },
    },
    required: ['text'],
  },
  requirements: [],
  timeout_seconds: null,
  group_name: '通用文件',
  is_active: true,
}

export function buildExampleBundleJson(): string {
  return JSON.stringify(
    { schema: 'nanzi.tool.bundle', schema_version: 1, tools: [EXAMPLE_SCRIPT_TOOL] },
    null,
    2,
  )
}

const PROMPT_TAIL = [
  '',
  '## 输出前自检',
  '- name 符合正则且是 snake_case；',
  '- code 能从 stdin 读 JSON、stdout 输出 JSON；',
  '- parameter_schema 的 required 与代码实际读取的参数一致；',
  '- requirements 与代码 import 的第三方库一致（没有第三方库就是 []）；',
  '- 写文件的脚本在 artifacts 里登记了相对路径；',
  '- 整个回复只有一个 JSON 代码块。',
  '',
  '注意：不确定的地方直接在 description 里写清限制，不要靠猜参数名或凭空增加必需依赖。',
]

export function buildScriptToolPrompt(requirement: string): string {
  const need = requirement.trim() || SCRIPT_PROMPT_PLACEHOLDER
  return [
    ...PROMPT_HEAD,
    '',
    '## 参考示例（结构与契约示意，内容不要照抄）',
    '下面这个工具把文本写成文件。重点看四件事：code 从 stdin 读参数、',
    'parameter_schema 里的参数名与 code 一致、文件写进工作区、artifacts 里登记的相对路径。',
    '',
    buildExampleBundleJson(),
    '',
    '## 需求',
    '按「功能 / 输入参数 / 输出 / 特殊要求」写，参数逐个列出类型与默认值；',
    '用户没写到的边界（超时、超长、空值）由你按常识补上并在 description 里说明。',
    '',
    need,
    ...PROMPT_TAIL,
  ].join('\n')
}
