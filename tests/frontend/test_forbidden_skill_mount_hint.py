"""技能挂载提示的路径口径：**一律不给文件系统路径**。

两层原因：

1. 被禁技能（本文件最初盯的缺口）——带路径等于每轮都在告诉模型"这个被禁技能可以读"，
   即使注入层已经拦住了技能；这段 content 还会随消息落进会话历史、不可恢复。
2. 所有技能——曾经写死的 `/app/data/skills/<id>/SKILL.md` 只在「平台容器 + 公共技能根」
   这一种组合下成立：个人技能的真身在 `agent_workspaces/<user_key>/skills/`，Docker/K8s
   沙箱里连 `/app/data` 都没有（只有挂到 `/workspace` 的用户工作区）。模型照抄这条路径
   只会拿到 `File does not exist`——而技能正文本来就已预载进上下文，这次读取纯属多余。

这里在 node harness 里跑**真实**的 TypeScript 实现（连同它的 import 图一起解析），
只做纯函数行为断言，不挂载组件。判定沿用 `skillForbidden.isSkillForbidden`：
只有严格布尔 true 才算禁用，缺字段（个人技能 / 旧接口）一律按未禁用处理。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

LOADER = """
const fs = require('fs');
const path = require('path');
const ts = require('./frontend/node_modules/typescript');

const cache = {};
function loadModule(relPath) {
  const key = relPath.endsWith('.ts') ? relPath : relPath + '.ts';
  if (cache[key]) return cache[key].exports;
  const code = ts.transpileModule(fs.readFileSync(key, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const moduleRef = { exports: {} };
  cache[key] = moduleRef;
  const localRequire = (specifier) => {
    let resolved;
    if (specifier.startsWith('@/')) resolved = 'frontend/src/' + specifier.slice(2);
    else resolved = path.posix.join(path.posix.dirname(key), specifier);
    if (!resolved.endsWith('.ts')) resolved += '.ts';
    return loadModule(resolved);
  };
  new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, localRequire);
  return moduleRef.exports;
}

const attachments = loadModule('frontend/src/composables/chat/useChatAttachments.ts');
const images = loadModule('frontend/src/utils/attachmentImages.ts');
"""


def _run_typescript(expression: str):
    script = f"""
(async () => {{
{LOADER}
const api = {{ attachments, images }};
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _hint_for(file_literal: str) -> dict:
    result = _run_typescript(
        f"""
const file = {file_literal};
const build = api.attachments.useChatAttachments({{ buildKnowledgeBaseAttachmentHint: (line) => line }});
return {{
  hint: build.appendAttachmentContext('问题', [file]),
  path: api.images.getServerAttachmentPath(file),
}};
"""
    )
    return result


FORBIDDEN_HINT_CASES = {
    "skillMeta 标记": (
        "{ type: 'skill', url: 'gamma', filename: 'Gamma (技能)', scope: 'global',"
        " skillMeta: { id: 'gamma', name: 'Gamma', scope: 'global', forbidden: true } }"
    ),
    "附件直挂标记": (
        "{ type: 'skill', url: 'gamma', filename: 'Gamma (技能)', forbidden: true }"
    ),
}


@pytest.mark.parametrize("case_name", list(FORBIDDEN_HINT_CASES))
def test_forbidden_skill_mount_never_carries_a_readable_path(case_name):
    result = _hint_for(FORBIDDEN_HINT_CASES[case_name])

    assert result["path"] == "", "被禁技能不再解析出任何绝对路径"
    hint = result["hint"]
    assert "/app/data/skills/" not in hint, f"{case_name}：提示里不能出现被禁技能的正文路径"
    assert "SKILL.md" not in hint
    assert "绝对路径" not in hint
    assert "已被管理员禁用" in hint or "已禁用" in hint
    assert "不可读取" in hint, "必须明确告知模型这是不可读取的"
    assert "问题" in hint, "用户正文仍要保留"


def test_forbidden_skill_hint_still_names_the_skill_so_the_model_can_explain():
    """禁用提示要可解释：带上技能名，模型才能回答"为什么用不了"。"""
    hint = _hint_for(FORBIDDEN_HINT_CASES["skillMeta 标记"])["hint"]

    assert "Gamma" in hint
    assert "Gamma (技能)" not in hint, "技能名要去掉「(技能)」后缀"


def test_available_skill_mount_never_carries_a_filesystem_path():
    """反向断言：未禁技能照旧挂载、照旧带 meta，但**不解析任何文件系统路径**。

    曾经这里返回 `/app/data/skills/<id>/SKILL.md`——那条路径只在「平台容器 + 公共技能根」
    下成立，个人技能（真身在 agent_workspaces/<user_key>/skills/）与 Docker/K8s 沙箱
    （只有 /workspace）里都不存在，模型照抄必然 File does not exist。
    """
    result = _hint_for(
        "{ type: 'skill', url: 'alpha', filename: 'Alpha (技能)', scope: 'global',"
        " skillMeta: { id: 'alpha', name: 'Alpha', description: '演示', scope: 'global' } }"
    )

    assert result["path"] == "", "技能挂载不该再解析出任何文件系统路径"
    hint = result["hint"]
    assert "/app/data/skills" not in hint
    assert "技能 ID：alpha" in hint
    assert "已随本轮上下文预载" in hint, "必须告诉模型正文已经预载，不要去读文件"
    assert "read_skill_instruction" in hint, "读附带脚本/资源要走技能资源读取入口（path 参数）"
    assert "skills meta 为：name: Alpha, description: 演示" in hint


@pytest.mark.parametrize(
    "case_name,file_literal",
    [
        (
            "显式 false",
            "{ type: 'skill', url: 'alpha', filename: 'Alpha (技能)',"
            " skillMeta: { id: 'alpha', name: 'Alpha', forbidden: false } }",
        ),
        (
            "字符串 false",
            "{ type: 'skill', url: 'alpha', filename: 'Alpha (技能)',"
            " skillMeta: { id: 'alpha', name: 'Alpha', forbidden: 'false' } }",
        ),
        (
            "个人技能不下发该字段",
            "{ type: 'skill', url: 'p1', filename: '我的技能 (技能)', scope: 'personal',"
            " skillMeta: { id: 'p1', name: '我的技能', scope: 'personal' } }",
        ),
    ],
)
def test_not_forbidden_skills_are_not_marked_unusable(case_name, file_literal):
    result = _hint_for(file_literal)

    assert result["path"] == "", f"{case_name} 不该被当成禁用"
    hint = result["hint"]
    assert "已调用生态技能工作流" in hint, f"{case_name} 不该被换成禁用文案"
    assert "不可读取" not in hint
    assert "已禁用" not in hint


def test_other_attachment_types_are_untouched():
    """禁用逻辑只作用于公共技能挂载，其它附件提示不能受影响。"""
    result = _run_typescript(
        """
const build = api.attachments.useChatAttachments({ buildKnowledgeBaseAttachmentHint: (line) => line });
return {
  localFile: build.appendAttachmentContext('问题', [
    { type: 'local_file', url: '/app/data/uploads/report.pdf', filename: 'report.pdf' }
  ]),
  localDir: build.appendAttachmentContext('问题', [
    { type: 'local_dir', url: '/app/data/uploads/dir', filename: 'dir' }
  ]),
  upload: build.appendAttachmentContext('问题', [
    { type: 'upload', url: '/static/uploads/a.png', filename: 'a.png', ext: 'png' }
  ]),
};
"""
    )

    assert "/app/data/uploads/report.pdf" in result["localFile"]
    assert "/app/data/uploads/dir" in result["localDir"]
    assert "/app/data/uploads/a.png" in result["upload"]


def test_chat_input_propagates_the_forbidden_flag_into_skill_meta():
    """源码级契约：技能中心挂载入口必须把后端的 forbidden 一起带进 skillMeta。

    否则附件上没有禁用标记，前端仍会生成"绝对路径可直接读"的提示（后端虽然会在写历史前
    再清洗一次，但这条路不该只靠后端兜底）。个人技能走的是另一条新建路径，不带该字段。
    """
    source = (ROOT / "frontend/src/components/embed/ChatInput.vue").read_text(encoding="utf-8")
    mount_body = source.split("const mountSkillFromCascade", 1)[1].split("\n};", 1)[0]

    assert "forbidden: skill.forbidden === true" in mount_body
