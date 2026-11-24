"""Contract: 任务表单两栏引导（要做什么 / 输出有什么要求）。

用户在 2026-11-17 反馈「执行指令」「任务约束指令」两个字段不够清晰、不知道怎么填，
改造后主标签改成白话、术语降为副标，并加了常驻提示条、预设快捷项与位置纠偏提示。
设计说明：docs/superpowers/specs/2026-11-17-task-prompt-field-clarity-design.md
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK_CENTER = ROOT / "frontend" / "src" / "views" / "TaskCenter.vue"
TASK_COMPOSER = ROOT / "frontend" / "src" / "components" / "task" / "TaskPromptComposer.vue"
TASK_API = ROOT / "frontend" / "src" / "api" / "task.ts"


def _task_center_text() -> str:
    return TASK_CENTER.read_text(encoding="utf-8")


def test_task_center_exposes_constraints_field_and_help_entry():
    text = _task_center_text()

    assert "taskConstraints" in text
    assert "showConstraintsHelpModal" in text
    assert 'title="查看填写说明与示例"' in text
    assert 'maxlength="2000"' in text


def test_two_columns_use_plain_labels_with_term_as_subtitle():
    """主标签说人话，内部术语降级为副标——这是本次反馈的核心。"""
    text = _task_center_text()

    # 主标签
    assert ">要做什么</label>" in text
    assert ">输出有什么要求</label>" in text
    # 术语仍在，但只作副标，避免老用户与文档对不上
    assert "必填 · 执行指令" in text
    assert "选填 · 任务约束，留空不影响任务" in text
    # 旧的等权重 uppercase 标签不应再出现
    assert "执行指令 (Prompt)" not in text
    assert ">任务约束指令</label>" not in text


def test_each_column_has_standing_guide_line():
    text = _task_center_text()

    assert "查什么、什么范围、要什么结果" in text
    assert "那边才会强制生效" in text
    assert "只写" in text and "不写任务" in text
    assert "直接留空" in text
    assert "留空即不生效" in text


def test_placeholder_teaches_by_example_not_by_noun_list():
    text = _task_center_text()
    composer = TASK_COMPOSER.read_text(encoding="utf-8")

    assert "例如：用无序列表，不要图表；金额统一按万元、保留两位小数。" in text
    # 旧占位符是四个维度堆一行，已替换
    assert "用无序列表呈现、按移动端阅读排版；禁止输出图表代码块" not in text
    assert "例如：查华东一号机房昨天的 PUE 峰值与均值，列出告警次数 Top3。" in composer


def test_constraint_presets_append_in_place():
    text = _task_center_text()

    assert "constraintPresets" in text
    assert "appendConstraintPreset" in text
    for label in ("移动端列表", "只要结论", "不要图表", "金额统一万元", "只看华东区"):
        assert label in text
    # 重复追加要有提示，不能悄悄重复写入
    assert "这条已经在输出要求里了" in text


def test_format_advice_moves_sentence_to_second_column():
    text = _task_center_text()

    assert "PROMPT_FORMAT_ADVICE_RE" in text
    assert "promptFormatAdviceSentence" in text
    assert "promptFormatAdviceVisible" in text
    assert "moveAdviceToConstraints" in text
    assert "移到第 2 栏" in text
    # 「忽略」只抑制当前这句，不做持久化
    assert "dismissedAdviceSentence" in text
    # 正则不能把「图表」「表格」当成独立命中项（查询图表数据是正常业务）
    assert "图表|表格|画图" in text or "图表|画图" in text


def test_help_modals_explain_split_with_side_by_side_table():
    text = _task_center_text()

    assert 'title="要做什么 · 填写示例"' in text
    assert 'title="输出要求 · 填写说明"' in text
    assert "同一件事，两栏分别写" in text
    # 对照表最后一行的「留空」示范：不是每栏都必须填
    assert "（留空）—— 没要求就别写" in text
    assert "以这里为准" in text
    for token in ("输出形态", "业务口径", "数据范围", "禁止事项"):
        assert token in text
    assert "需要强制生效的格式要求，请写在这里" in text


def test_prompt_templates_do_not_teach_format_into_first_column():
    """模板若把格式要求写进第 1 栏，就和新的引导自相矛盾。"""
    text = _task_center_text()

    assert "【输出要求】" not in text
    assert "【结果形式】" in text
    assert "排版 / 单位 / 不要图表这类要求请写到下方「输出有什么要求」" in text


def test_task_center_persists_constraints_in_config():
    text = _task_center_text()

    assert "baseConfig.task_constraints" in text
    assert "delete baseConfig.task_constraints" in text
    assert "cfg.task_constraints" in text


def test_task_center_marks_tasks_with_constraints():
    """卡片上的标记必须和弹窗同一套说法：弹窗叫「输出有什么要求」，卡片上就别缩成「约束」，
    否则用户认不出这是自己配过的那一栏。"""
    text = _task_center_text()

    assert "task.config?.task_constraints" in text
    assert "已设输出要求" in text
    assert "已设置「输出有什么要求」" in text


def test_task_api_config_type_declares_constraints():
    text = TASK_API.read_text(encoding="utf-8")

    assert "task_constraints?: string" in text
