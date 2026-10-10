"""工具入参（Bash 命令）在会话执行时间线上的展示契约。

用户排查「这条 Bash 到底跑了什么命令」时，时间线卡片此前只展示工具输出，
命令本身既不在副本里也不在事件里。命令必须作为独立字段 tool_args 透传，
与 details（工具输出）互不覆盖。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _can_render_sfc() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all((node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer"))


def test_backend_tool_events_carry_tool_args_for_timeline_cards():
    runner = _read("app/services/ai/runners/assistant_agent_runner.py")
    assert "format_tool_args_for_display" in runner
    assert 'log_event["tool_args"]' in runner

    stream = _read("app/services/ai/runtime/agentscope/event_stream.py")
    assert "format_tool_args_for_display" in stream
    assert 'start_log["tool_args"]' in stream
    # 写死的假占位必须消失，否则进行中的卡片会显示「参数: {}」。
    assert '"参数: {}"' not in stream


def test_frontend_forwards_tool_args_from_sse_into_timeline_items():
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "tool_args: data.tool_args" in handlers

    timeline = _read("frontend/src/utils/processTimeline.ts")
    assert "tool_args?: string;" in timeline


def test_timeline_component_renders_tool_args_block():
    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "TimelineToolArgsBlock" in component
    assert "timelineArgsText" in component

    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")
    assert "{{ text }}" in block
    assert "命令" in block
    assert "参数" in block


def test_details_copy_button_never_shares_container_with_args_block():
    """入参区块与「工具输出」复制按钮必须分属不同 relative 容器。

    两者同容器时，输出复制按钮（absolute right-1 top-1.5 z-10）会几何重叠并
    盖住入参区块自己的复制按钮，点击「复制命令」实际复制的是工具输出。
    """
    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    detail_containers = [
        line
        for line in component.splitlines()
        if 'class="group/details relative' in line and "border-t" in line
    ]
    assert len(detail_containers) == 4, "四个层级的输出区块都要独立成容器"
    for line in detail_containers:
        assert "hasVisibleTimelineText" in line
        assert "hasTimelineArgs" not in line, (
            "输出复制按钮的容器条件不得包含入参判断，否则会在只有入参时渲染出挡住的空按钮"
        )


def test_tool_args_copy_button_has_explicit_accessible_name():
    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")
    assert "'复制' + label" in block


def test_secondary_tool_emitters_forward_tool_args_too():
    """知识库 / ChatBI 链路同样把命令、SQL 这类入参透传到卡片，避免只有主链路可见。"""
    knowledge = _read("app/services/ai/runners/knowledge_agent_runner.py")
    # 知识库复用父类 _build_tool_observation 的结果，从中透传入参即可。
    assert 'observation.get("log", {}).get("tool_args")' in knowledge

    for path in (
        "app/services/ai/runners/chatbi/react_stream.py",
        "app/services/ai/runners/chatbi/schema_prefetch.py",
    ):
        source = _read(path)
        assert "format_tool_args_for_display" in source, path
        assert "tool_args" in source, path


def test_tool_args_block_collapses_independently_from_its_timeline_row():
    """入参区块自带折叠开关，独立于所属时间线行的展开状态。

    「连续未命中数据集定义」这类失败行展开后，命令、SQL 这类入参可能很长；
    用户想只收起入参、继续看错误原因、模型元信息和工具输出时，此前只能整行收起。
    """
    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")

    assert "bodyExpanded" in block, "入参区块需要属于自己的折叠状态"
    assert "ref(true)" in block, "默认展开，避免升级后已有卡片的参数凭空消失"
    assert 'v-show="bodyExpanded"' in block, "正文显隐必须由本区块自己的状态决定"
    assert ':aria-expanded="bodyExpanded"' in block, "折叠开关需要无障碍展开态"
    assert '@click="bodyExpanded = !bodyExpanded"' in block, "标题行需要可点击切换折叠"

    # 折叠开关与复制按钮必须解耦：点复制不得顺带折叠或展开。
    assert """@click.stop="emit('copy', copyKey, text)\"""" in block

    # 入参区块不得反向依赖父级的展开标志，否则又会退化成「只能整行折叠」。
    assert "isExpanded" not in block
    assert "childrenExpanded" not in block


@pytest.mark.skipif(not _can_render_sfc(), reason="需要 node 与 frontend/node_modules 里的 vue / esbuild / @vue/server-renderer 才能真渲染 SFC")
def test_tool_args_block_really_hides_body_when_its_own_state_is_false():
    """真渲染一次：确认折叠开关真的接在正文上，而不是只把字写在源码里。

    字符串断言看不出 v-show 绑成了常量、或正文退化成无条件渲染。SSR 没有 DOM 事件，
    所以「折叠态」是**把默认值改成 false 后再渲染真实组件**得到的 —— 收起态若不隐藏正文
    就说明开关没接上，这条用例会立刻变红。
    """
    script = ROOT / "tests/frontend/timeline_tool_args_collapse.cjs"
    proc = subprocess.run(["node", str(script)], cwd=ROOT / "frontend", capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    expanded = data["expanded"]
    assert expanded["ariaExpanded"] == "true", "入参区块默认必须是展开态"
    assert expanded["toggleTitle"] == "收起参数", "展开态应提示可收起"
    assert expanded["bodyHidden"] is False, "默认展开时正文不得被隐藏"
    assert expanded["bodyRendered"] is True, "默认展开时参数正文必须真的渲染出来"

    collapsed = data["collapsed"]
    assert collapsed["ariaExpanded"] == "false"
    assert collapsed["toggleTitle"] == "展开参数", "折叠态应提示可展开"
    assert collapsed["bodyHidden"] is True, (
        "自折叠状态为 false 时正文必须隐藏 —— v-show 没接在 bodyExpanded 上就会在这里变红"
    )
    # v-show 语义：折叠只是不显示，正文仍在 DOM 里，复制按钮照旧可用。
    assert collapsed["bodyRendered"] is True, "折叠应只是隐藏（v-show），正文不该被卸载"
    assert collapsed["copyAriaLabel"] == "复制参数"

    command = data["command"]
    assert command["label"] == "命令", "Bash 类工具仍应显示为「命令」而非「参数」"
    assert command["copyAriaLabel"] == "复制命令"


def test_own_args_precede_children_while_output_stays_after():
    """自身入参（+元信息）必须排在子步骤之前，工具输出留在子步骤之后。

    委派工具的 `{"agent_name": "chat-bi", "query": "…"}` 原先渲染在所有内部步骤之后，
    紧贴在最后一个子步骤（「连续未命中数据集定义」）下方，被读成那个子步骤的参数。
    阅读顺序应为「输入 → 过程 → 输出」。
    """
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    cases = (
        (
            "子代理/工具子卡片",
            "(hasTimelineArgs(child) || timelineMetaText(child)) && isChildDetailsOpen(child)",
            "`child-args-${child.id}`",
            'v-if="child.children?.length && child.childrenExpanded !== false"',
            '@click.stop="handleCopy(`child-${child.id}`, visibleTimelineText(',
        ),
        (
            "顶级时间线项（委派智能体容器）",
            "(hasTimelineArgs(item) || timelineMetaText(item)) && isTimelineItemDetailsOpen(item)",
            "`item-args-${item.id}`",
            'v-if="item.children?.length && item.childrenExpanded !== false"',
            '@click.stop="handleCopy(`item-${item.id}`, visibleTimelineText(',
        ),
    )

    for name, args_wrapper, args_key, children_marker, details_click in cases:
        wrapper_at = timeline.index(args_wrapper)
        args_at = timeline.index(args_key)
        children_at = timeline.index(children_marker)
        details_at = timeline.index(details_click)

        assert wrapper_at <= args_at < children_at, f"{name}：自身入参与元信息必须排在子步骤之前"
        assert details_at > children_at, f"{name}：工具输出应留在子步骤之后（输入 → 过程 → 输出）"


def test_substep_with_nested_steps_shares_child_expansion_semantics():
    """subStep 下有更深子步骤时，自身入参与输出也必须看得见。

    此前 subStep 的点击只切 `childrenExpanded`，`isExpanded` 永远是 false，于是
    `subStep.isExpanded && !subStep.children?.length` 让这类步骤的入参/输出彻底打不开
    —— 去掉 `!children?.length` 也修不好，两层原因必须一起解决。
    """
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    # 复用第二层（child）已验证过的语义，而不是再写一份同构实现
    assert "const isSubStepDetailsOpen = isChildDetailsOpen;" in timeline, "第三层未复用第二层的展开判定"
    assert "const toggleSubStepItem = toggleChildItem;" in timeline, "第三层未复用第二层的点击语义"
    assert ':aria-expanded="isSubStepDetailsOpen(subStep)"' in timeline
    assert '@click="toggleSubStepItem(subStep)"' in timeline

    # 旧写法必须彻底消失：既不能吞掉详情，也不能再内联一份只切 childrenExpanded 的分支
    assert "subStep.isExpanded && !subStep.children?.length" not in timeline, "有子步骤就不给看自身详情的写法必须移除"
    assert "subStep.children?.length ? (subStep.childrenExpanded" not in timeline, "不得内联只切 childrenExpanded 的分支"


def test_substep_args_precede_nested_steps_while_output_stays_after():
    """第三层同样遵守「输入 → 过程 → 输出」（取顶级项路径那一处，child 路径没有更深子步骤）。"""
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    args_at = timeline.rindex("`substep-args-${subStep.id}`")
    nested_at = timeline.index('v-if="subStep.children?.length && subStep.childrenExpanded !== false"')
    output_at = timeline.rindex('@click.stop="handleCopy(`substep-${subStep.id}`, visibleTimelineText(')

    assert args_at < nested_at, "子步骤自身的入参必须排在更深子步骤之前"
    assert output_at > nested_at, "子步骤的工具输出应留在更深子步骤之后"


def test_child_tool_with_children_substeps_renders_own_args_and_details():
    """当工具节点（如 Bash）下方挂载子步骤（如沙箱工作区准备）时，其自身的入参和输出不得被互斥条件吞掉。"""
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "child.isExpanded && !child.children?.length" not in timeline, "不得使用 !child.children?.length 拦截子卡片自身的入参与详情渲染"
    assert "item.isExpanded && !item.children?.length" not in timeline, "不得使用 !item.children?.length 拦截顶级项自身的入参与详情渲染"
    assert "isChildDetailsOpen" in timeline
    assert "toggleChildItem" in timeline


DIV_TOKEN = re.compile(r"<div\b|</div>")


def _div_block_end(text: str, start: int) -> int:
    """返回从 start 处的 `<div` 起、与之配对的 `</div>` 之后的下标。"""
    depth = 0
    for token in DIV_TOKEN.finditer(text, start):
        if token.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                return token.end()
        else:
            depth += 1
    raise AssertionError("参数盒的 div 没有闭合")


def test_args_strip_lives_on_the_container_and_groups_meta_inside():
    """参数区完全去盒子：用左侧细竖线 + 缩进表达层级，元信息收在同一块内。

    用户明确要求「保留铺满、完全去盒子（走左侧细竖线 + 缩进）」：参数区不得再出现
    边框 / 底色 / 宽度限制类，组件自身也不得画框（否则会出现盒中盒、或说明掉在框外）。
    """
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")

    strip_class = "mt-1 border-l-2 border-gray-200/80 pl-2.5 dark:border-gray-700/80"
    meta_class = 'class="mt-1 break-words text-[10px] text-gray-400 dark:text-gray-500"'

    strips = list(re.finditer(re.escape(strip_class), timeline))
    assert len(strips) == 5, "四个层级（含第四层）必须共用同一套参数区样式"
    assert timeline.count(meta_class) == 5, "每个参数区都应带自己的元信息行"

    for strip in strips:
        div_start = timeline.rindex("<div", 0, strip.start())
        div_end = _div_block_end(timeline, div_start)
        meta_at = timeline.find("timelineMetaText(", strip.end())
        assert 0 < meta_at < div_end, "元信息必须收在同一个参数区内，不能留在外面"

    # 铺满：参数区不得用宽度限制类收窄（短参数也不收）
    assert "w-fit" not in strip_class and "max-w-" not in strip_class
    assert "max-w-4xl" not in timeline, "上一版的收窄盒子已废弃"

    # 完全去盒子：父级与组件都不得再画框
    assert "rounded border border-gray-200/70" not in timeline
    assert "rounded border border-gray-200/70 bg-gray-50/70" not in block
    assert "w-fit" not in block

