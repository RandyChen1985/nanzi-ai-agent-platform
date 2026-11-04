"""EmbedChat 输入框：当前专家是团主专家时，胶囊上的图标要换成「团队」。

为什么需要：那个胶囊（人在左下角、显示 "dddd" 的那个）原来固定用单人图标，
可团主专家背后是一整个团在调度——图标说「一个人」，横幅说「5 位子专家」，
两处信息打架。现在按 `is_expert_team_lead` 换成团队图标。

分支必须是三分支而不是两分支：团主专家 / 普通专家 / 智能委派（闪电），
缺一个都会让某种状态显示错图标。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CHAT_INPUT = ROOT / "frontend/src/components/embed/ChatInput.vue"

# heroicons outline「users」：与 ChatSettings「多智能体协同」用的是同一个图标
TEAM_ICON = "M17 20h5v-2a3 3 0 00-5.356-1.857"
# heroicons outline「user」：普通专家仍在用
USER_ICON = "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"


def _read() -> str:
    assert CHAT_INPUT.exists(), f"文件不存在：{CHAT_INPUT}"
    return CHAT_INPUT.read_text(encoding="utf-8")


def test_team_lead_flag_is_derived_from_selected_agent():
    source = _read()
    assert "const currentExpertIsTeamLead = computed(" in source
    # 判据必须是服务端给的 is_expert_team_lead，不能靠「有 team_name」推断：
    # 团主专家可能还没填团名，普通智能体的 team_name 才是 null，两者都推不准
    assert "currentExpertAgent.value?.is_expert_team_lead === true" in source


def test_capsule_icon_has_three_branches():
    source = _read()
    start = source.index("<!-- 团主专家用「团队」图标")
    block = source[start:start + 2200]
    # 团主专家 → 团队图标
    assert 'v-if="currentExpertIsTeamLead"' in block
    assert TEAM_ICON in block
    # 普通专家 → 单人图标（不能因为加了团队分支就把单人图标删掉）
    assert 'v-else-if="isExpertMode"' in block
    assert USER_ICON in block
    # 其余 → 闪电（BoltIcon 仍是 else 分支，位置在团队图标之前会抢断条件）


def test_new_api_field_is_exposed_by_backend():
    """前端判断依赖 `is_expert_team_lead` 出现在 /agents/allowed 的响应里。

    该接口走 AIAgentResponse，字段必须声明在基类上，否则响应模型会把字段丢掉，
    前端永远拿到 undefined、图标永远是单人。
    """
    schemas = (ROOT / "app/schemas/agent.py").read_text(encoding="utf-8")
    assert "is_expert_team_lead" in schemas
    base_start = schemas.index("class AIAgentBase")
    response_start = schemas.index("class AIAgentResponse")
    assert base_start < schemas.index("is_expert_team_lead", base_start)
    # 字段在基类里（AIAgentResponse 继承它），不是只出现在别的模型上
    assert schemas.index("is_expert_team_lead", base_start) < response_start
