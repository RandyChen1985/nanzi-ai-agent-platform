"""方案 C「模板起步」入口的纯文本契约。

用户路径：新建定时任务 → 先看到模板卡（也含「从空白开始」）→ 选一个 → 落到正常表单。
这里只断言源码里必须存在的结构与文案，不启动前端。
"""

from pathlib import Path

PAGE = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "views" / "TaskCenter.vue"
).read_text(encoding="utf-8")


def test_template_catalog_exists_with_five_templates():
    """模板目录：5 个业务模板，每个都要有名称/时间/提示词/输出要求。"""
    assert "const taskTemplates = [" in PAGE
    for title in [
        "每日 / 每周指标报表",
        "阈值巡检 / 告警",
        "周期汇总简报",
        "失败盘点 / 质量复查",
        "外部信息巡检",
    ]:
        assert title in PAGE, f"缺少模板：{title}"
    # 每个模板必须四件套齐全，否则套用后会留下空字段
    for key in ["name:", "cronExpr:", "prompt:", "constraints:", "summary:"]:
        assert PAGE.count(key) >= 5, f"模板字段 {key} 数量不足 5"


def test_template_cron_expressions_are_standard_five_field():
    """模板里的 cron 只用标准 5 段，且能被「运行周期」的四种人话模式回填。"""
    import re

    exprs = re.findall(r"cronExpr: '([^']+)'", PAGE)
    assert exprs, "没有找到任何模板 cron"
    assert len(exprs) == 5
    for expr in exprs:
        parts = expr.split()
        assert len(parts) == 5, f"{expr} 不是 5 段 cron"
        # 模板必须能回填到 每天 / 每周 / 间隔 三种模式之一，不能落到「高级」
        minute, hour, dom, _month, dow = parts
        simple = (
            (minute.isdigit() and hour.isdigit() and dom == "*" and dow == "*")
            or (minute.isdigit() and hour.isdigit() and dom == "*" and dow.isdigit())
            or (minute == "0" and hour.startswith("*/") and dom == "*")
        )
        assert simple, f"{expr} 无法回填到人话模式，用户会掉进「高级」cron"


def test_template_picker_is_the_first_screen_for_create_only():
    """新建才过模板页；编辑已有任务直接进表单（弹窗状态可能从上一次新建残留）。"""
    assert "const showTemplatePicker = ref(false)" in PAGE
    assert "showTemplatePicker.value = true" in PAGE
    # 新建入口打开模板页
    create_block = PAGE.split("const openCreateModal")[1].split("const openEditModal")[0]
    assert "showTemplatePicker.value = true" in create_block
    # 编辑入口显式关掉模板页
    edit_block = PAGE.split("const openEditModal")[1].split("const getTaskEditTitle")[0]
    assert "showTemplatePicker.value = false" in edit_block
    # 关闭弹窗时复位，避免下次打开沿用上次的视图
    assert "const closeEditModal = () => {" in PAGE
    close_block = PAGE.split("const closeEditModal = () => {")[1].split("}")[0]
    assert "showEditModal.value = false" in close_block
    assert "showTemplatePicker.value = false" in close_block


def test_template_picker_markup_has_blank_option_and_prefill_note():
    """模板页必须有两个出口：选模板 / 从空白开始，并说明【】要替换。"""
    assert 'v-if="showTemplatePicker"' in PAGE
    assert 'v-for="tpl in taskTemplates"' in PAGE
    assert "从空白开始" in PAGE
    assert "@click=\"startBlankTask\"" in PAGE
    assert "@click=\"applyTaskTemplate(tpl)\"" in PAGE
    assert "【】里的内容需要你换成自己的对象" in PAGE
    # 模板页不显示「确认保存」，避免没填就保存
    assert '<div v-if="!showTemplatePicker" class="flex justify-end' in PAGE


def test_apply_template_fills_fields_and_syncs_cron_ui():
    """套用模板必须同时写 4 个字段，并把 cron 回填到人话控件。"""
    body = PAGE.split("const applyTaskTemplate")[1].split("const startBlankTask")[0]
    assert "editingTask.value.name = template.name" in body
    assert "editingTask.value.prompt = template.prompt" in body
    assert "editingTask.value.cron_expr = template.cronExpr" in body
    assert "taskConstraints.value = template.constraints" in body
    # 只写表达式不回填控件，会出现「选的是每天、表达式是每周」的错位
    assert "parseCronToUI(template.cronExpr)" in body
