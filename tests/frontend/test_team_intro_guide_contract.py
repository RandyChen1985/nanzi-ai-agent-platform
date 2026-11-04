"""「团队介绍&提示词」写法指引的内容契约。

这段指引本身就是一份提示词写作规范，而它服务的对象——团介绍——已经出过事故：
某团主在介绍里写了「不查不写」，模型把「我不动手」读成了「我不委派」，配好 4 位
子专家却只回一句「请你自己去【资产查询】办理」。

所以指引不能只讲「该怎么写」，还要讲「哪些写法会把它劝住」，并给出可直接套用的
多场景模板。这里守住内容结构：三条法则、破坏性写法（带后果与替代写法）、多场景
模板（每个都能一键填入）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

GUIDE = Path("frontend/src/components/agent/TeamIntroGuide.vue").read_text()


def test_guide_keeps_the_three_rules():
    assert "三条法则" in GUIDE
    assert "写「什么情况找谁」和先后顺序" in GUIDE
    assert "写禁止项" in GUIDE
    assert "短句祈使句" in GUIDE


def test_guide_lists_destructive_prompts_with_consequence_and_fix():
    """每条都要讲清「会发生什么」和「改成什么」，否则用户只会照抄危险写法。"""
    assert "这些写法会「劝住」团主" in GUIDE
    assert "会发生什么" in GUIDE
    assert "改成：" in GUIDE

    # 真实事故案例必须在里面
    assert "不查不写" in GUIDE
    assert "把「我不动手」读成了「我不委派」" in GUIDE

    # 其余典型坑：全量转交 / 直选泛化 / 抄成员名单 / 空壳团主 / 自相矛盾
    for needle in ("所有需求一律转给", "必须直选，禁止委派", "把成员名单抄进介绍", "严禁回答任何业务问题", "自相矛盾"):
        assert needle in GUIDE, needle


def test_guide_offers_multiple_fillable_scenarios():
    assert "按场景套用" in GUIDE
    for label in (
        "数据查询型",
        "查询 + 写操作混合",
        "文档 / 清单产出",
        "客服 / 工单分流",
        "只读风控 / 合规审查",
    ):
        assert label in GUIDE, label

    # 每个场景都带自己的文本，点按钮填入的是那一版而不是固定文案
    assert GUIDE.count("text: [") == 5
    assert '@click="emit(\'apply\', item.text)"' in GUIDE


def test_guide_ends_with_a_self_check():
    assert "填写前自查三问" in GUIDE
    assert "那该由谁做" in GUIDE


def test_guide_still_supports_the_popup_usage():
    """弹层自带标题，组件要能用 hide-intro 隐掉自己那套标题。"""
    assert "hideIntro?" in GUIDE
    assert "v-if=\"!hideIntro\"" in GUIDE


def test_label_says_it_is_a_prompt_not_a_note():
    """名字里必须带「提示词」：只叫「团队介绍」会被当成备注随便写。"""
    for path in (
        "frontend/src/components/agent/AgentTeamEditorModal.vue",
        "frontend/src/views/AgentManagement.vue",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "团队介绍&amp;提示词" in source, path
        assert "团队介绍&提示词怎么写？" in source, path
        # 占位示例要能直接照抄，别再写「一条龙团队」这种无法执行的营销话术
        assert "一条龙团队" not in source, path
        assert "取数只读，禁止写库" in source, path
