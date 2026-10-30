"""技能流程徽标：界面文案统一说「技能」，历史日志仍认得旧措辞。

背景：执行过程里那条横幅原本写「本轮已启用流程：」，与其它位置一律称「技能」不一致，
用户看截图后要求把「流程」改成「技能」。

两个必须同时成立的方向：

1. **新写入的日志**（后端 `injector.build_skill_log_chunk`）标题改成「已启用技能: 」，
   界面文案跟「技能」口径一致；
2. **已经落库的历史消息**里标题仍是「已启用流程: 」——解析侧必须继续认得，否则老会话
   的技能徽标会突然消失（`process_timeline` 是随消息持久化的快照，不会重写）。

这里用 node 直接转译真实 TS 源码执行，不复刻实现。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/skillFlowBadges.ts"
pytestmark = pytest.mark.no_infrastructure


def _run(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ return await (async () => {{ {expression} }})(); }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_enabled_banner_and_summary_say_skills():
    """用户截图那一条：横幅必须说「本轮已启用技能：」。"""
    result = _run("""
const badges = api.buildSkillFlowBadges([], [{ title: '已启用技能: pdf' }]);
return {
  label: api.skillFlowNoticeLabel(badges),
  summary: api.summarizeSkillFlowBadges(badges),
  kinds: badges.map(b => b.kind),
}
""")

    assert result["label"] == "本轮已启用技能："
    assert result["summary"] == "1个技能已启用"
    assert result["kinds"] == ["enabled"]


def test_candidate_banner_and_summary_say_skills():
    result = _run("""
const badges = api.buildSkillFlowBadges([], [
  { title: '已识别候选技能: pdf' },
  { title: '已识别候选技能: 周报生成' },
]);
return {
  label: api.skillFlowNoticeLabel(badges),
  summary: api.summarizeSkillFlowBadges(badges),
}
""")

    assert result["label"] == "本轮识别到候选技能："
    assert result["summary"] == "2个候选技能"


def test_mixed_banner_no_longer_mentions_flows():
    result = _run("""
const badges = api.buildSkillFlowBadges(
  [{ type: 'skill', filename: 'pdf' }],
  [{ title: '已启用技能: 周报生成' }],
);
return {
  label: api.skillFlowNoticeLabel(badges),
  summary: api.summarizeSkillFlowBadges(badges),
}
""")

    assert result["label"] == "本轮关联技能："
    assert result["summary"] == "2个技能"


def test_manually_attached_skills_keep_their_wording():
    """手动挂载技能本来就不叫「流程」，不要被这次改动带偏。"""
    result = _run("""
const badges = api.buildSkillFlowBadges([{ type: 'skill', filename: 'pdf' }], []);
return {
  label: api.skillFlowNoticeLabel(badges),
  summary: api.summarizeSkillFlowBadges(badges),
}
""")

    assert result["label"] == "本轮已加载生态技能："
    assert result["summary"] == "1个技能已加载"


def test_legacy_flow_titles_still_resolve_to_badges():
    """历史快照里落库的仍是旧措辞，解析侧不能因此丢掉徽标。"""
    result = _run("""
const enabled = api.buildSkillFlowBadges([], [{ title: '已启用流程: pdf' }]);
const candidate = api.buildSkillFlowBadges([], [{ title: '已识别候选流程: pdf' }]);
return {
  enabledKinds: enabled.map(b => b.kind),
  enabledLabels: enabled.map(b => b.label),
  candidateKinds: candidate.map(b => b.kind),
  candidateLabels: candidate.map(b => b.label),
}
""")

    assert result["enabledKinds"] == ["enabled"]
    assert result["enabledLabels"] == ["pdf"]
    assert result["candidateKinds"] == ["candidate"]
    assert result["candidateLabels"] == ["pdf"]


def test_details_only_logs_are_read_in_both_wordings():
    """有的日志只有 details（标题被截断/旧快照），两种措辞都要认出技能名。"""
    result = _run("""
const fresh = api.buildSkillFlowBadges([], [
  { title: '技能匹配', details: '已启用技能「pdf」(ID: pdf)。' },
]);
const legacy = api.buildSkillFlowBadges([], [
  { title: '技能匹配', details: '已启用流程「pdf」(ID: pdf)。' },
]);
return { fresh: fresh.map(b => b.label), legacy: legacy.map(b => b.label) }
""")

    assert result["fresh"] == ["pdf"]
    assert result["legacy"] == ["pdf"]
