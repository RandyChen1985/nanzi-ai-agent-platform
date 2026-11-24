"""「我的技能」提交平台审核：确认框与按钮文案的口径。

抓的是两件此前会误导作者的事：

1. **点一下就直接进管理员审核队列**。提交按钮紧挨着启用开关，误点没有二次确认，撤销还得
   再点一次「撤销审核」；确认框要顺带说清「提交的是哪个目录的快照」「通过后平台版变成几」。
2. **`REVOKED`（管理员下架）落进了「提交为平台技能」分支**。后端会复用同一条 publication
   记录、版本号继续 +1，所以那次提交实际是「重新上架成 v(N+1)」，文案却像从头申请。
   同理，`PUBLISHED` 且 `pending_version` 有值时按钮仍显示「提交新版本」，点下去后端按
   「已有不同版本在审核中」返回 409 —— 是否待审必须把 `pending_version` 一起算进来。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "frontend/src/utils/skillPublication.ts"
VIEW = ROOT / "frontend/src/views/SkillsManagement.vue"
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(expression: str):
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


def test_action_label_covers_every_publication_status():
    """每种状态都要有准确文案：`UNPUBLISHED` 之外的兜底不能把「重新上架」说成首次申请。"""
    result = _run_typescript(
        """
return {
  published: api.skillPublicationActionLabel('PUBLISHED'),
  rejected: api.skillPublicationActionLabel('REJECTED'),
  revoked: api.skillPublicationActionLabel('REVOKED'),
  unpublished: api.skillPublicationActionLabel('UNPUBLISHED'),
  missing: api.skillPublicationActionLabel(undefined),
};
"""
    )

    assert result["published"] == "提交新版本"
    assert result["rejected"] == "提交新版本"
    assert result["revoked"] == "重新提交上架", "已下架再提交是重新上架，不是从头申请"
    assert result["unpublished"] == "提交为平台技能"
    assert result["missing"] == "提交为平台技能"


def test_pending_detection_respects_pending_version():
    """已上架但有一版在审（徽标显示「新版审核中」）也算待审，否则按钮会点出 409。"""
    result = _run_typescript(
        """
return {
  pending: api.isSkillSubmissionPending({ publication_status: 'PENDING' }),
  publishedWithPending: api.isSkillSubmissionPending({ publication_status: 'PUBLISHED', pending_version: 3 }),
  publishedClean: api.isSkillSubmissionPending({ publication_status: 'PUBLISHED' }),
  rejected: api.isSkillSubmissionPending({ publication_status: 'REJECTED' }),
  revokedWithPending: api.isSkillSubmissionPending({ publication_status: 'REVOKED', pending_version: 4 }),
  empty: api.isSkillSubmissionPending(null),
};
"""
    )

    assert result["pending"] is True
    assert result["publishedWithPending"] is True
    assert result["publishedClean"] is False
    assert result["rejected"] is False
    assert result["revokedWithPending"] is True
    assert result["empty"] is False


def test_confirm_copy_states_snapshot_scope_and_next_version():
    """确认框必须说清三件事：打包哪个目录、通过后变成 v几、提交后还能撤销。"""
    result = _run_typescript(
        """
return {
  upgrade: api.buildSkillSubmissionConfirm({
    id: 'dsapi-admin',
    path: 'data/skills/dsapi-admin',
    publication_status: 'PUBLISHED',
    current_public_version: 2,
  }),
  first: api.buildSkillSubmissionConfirm({
    id: 'fresh',
    path: 'data/skills/fresh',
    publication_status: 'UNPUBLISHED',
  }),
};
"""
    )

    upgrade = result["upgrade"]
    assert upgrade["title"] == "提交新版本审核"
    assert "data/skills/dsapi-admin" in upgrade["message"], "要说清打包的是哪个目录"
    assert "从 v2 升级为 v3" in upgrade["message"]
    assert "审核期间线上仍是 v2" in upgrade["message"], "别让作者以为提交即生效"
    assert "撤销审核" in upgrade["message"]

    first = result["first"]
    assert first["title"] == "提交为平台技能审核"
    assert "上架（v1）" in first["message"]
    assert "撤销审核" in first["message"]


def test_confirm_copy_for_revoked_and_rejected_carry_review_comment():
    """被下架/被驳回时把原因带回确认框：作者要基于原因决定改不改再提交。"""
    result = _run_typescript(
        """
return {
  revoked: api.buildSkillSubmissionConfirm({
    id: 'research',
    path: 'data/skills/research',
    publication_status: 'REVOKED',
    current_public_version: 2,
    last_review_comment: '描述缺少使用场景',
  }),
  rejected: api.buildSkillSubmissionConfirm({
    id: 'research',
    path: 'data/skills/research',
    publication_status: 'REJECTED',
    current_public_version: 0,
    last_review_comment: 'SKILL.md 缺少 name',
  }),
};
"""
    )

    revoked = result["revoked"]
    assert revoked["title"] == "重新提交上架审核"
    assert "已下架" in revoked["message"]
    assert "描述缺少使用场景" in revoked["message"], "下架原因要出现在确认框里"
    assert "重新上架为 v3" in revoked["message"]

    rejected = result["rejected"]
    assert rejected["title"] == "重新提交审核"
    assert "上次审核被驳回" in rejected["message"]
    assert "SKILL.md 缺少 name" in rejected["message"]
    assert "更新为 v1" in rejected["message"]


def test_success_toast_distinguishes_resubmission():
    """「重新提交」与「首次提交」的成功提示要分开，避免作者以为自己只是又点了一次。"""
    result = _run_typescript(
        """
return {
  revoked: api.skillSubmissionSuccessToast('REVOKED'),
  published: api.skillSubmissionSuccessToast('PUBLISHED'),
  rejected: api.skillSubmissionSuccessToast('REJECTED'),
  first: api.skillSubmissionSuccessToast('UNPUBLISHED'),
};
"""
    )

    assert "重新提交" in result["revoked"]
    assert "新版本" in result["published"]
    assert "新版本" in result["rejected"]
    assert result["first"] == "已提交管理员审核"


def test_skills_view_blocks_submission_behind_a_confirm_dialog():
    """源码契约：提交必须经过 `confirmState`，且按钮文案与待审判定都走纯函数。"""
    view = VIEW.read_text(encoding="utf-8")

    assert "buildSkillSubmissionConfirm(" in view
    assert "isSkillSubmissionPending(skill)" in view
    assert "skillPublicationActionLabel(" in view
    assert "skillSubmissionSuccessToast(" in view

    # 提交流程里必须挂确认框，且不存在「点一下直接 POST」的旧写法
    start = view.index("const submitSkillPublication =")
    block = view[start : view.index("\n}\n", start)]
    assert "confirmState.value = {" in block, "提交前必须弹确认框"
    assert "axios.post(" in block
    assert block.index("confirmState.value = {") < block.index("axios.post("), "确认在前、提交在后"

    # 内联三元文案已删干净（否则 REVOKED 会再退回「提交为平台技能」）
    assert "? '提交新版本' : '提交为平台技能'" not in view
    assert "skill.publication_status !== 'PENDING'" not in view
