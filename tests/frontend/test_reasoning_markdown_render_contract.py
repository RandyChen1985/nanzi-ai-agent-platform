"""思考展示改造的回归契约（Markdown 渲染 / 限高 / 轨道节点）。

这一组断言锁的是**渲染契约**，不是视觉细节：
1. 深度思考正文必须走与正文同一条 markdown 管线（renderMarkdown，内部已 sanitize），
   不能退回裸 `<pre>`——模型吐出的 `**加粗**`、`- 列表`、```sql 围栏会直接裸露；
2. 限高只在「超长且已落定」时生效：进行中不限高（流式最后一段还在增长，套固定高度会
   让新字落在可视区外），短思考点开即读全（不做「点开一行 → 再点展开」两次点击）；
3. 展开状态记在组件内，不写回时间线对象——时间线在流式合并与历史恢复时会被重建，
   写回的字段会被冲掉，表现成「点了展开又收回去」；
4. 轨道节点只靠形状区分（圆点 / 方块 / 小点 / 菱形），不引入色相；
5. 结构色一律半透明中性灰：既覆盖深浅两套主题，也不碰契约禁止的 `:global(.dark)`。
"""

from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/chat/ChatExecutionTimeline.vue"
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def _style_block() -> str:
    source = _source()
    start = source.index("<style scoped>")
    return source[start:]


def test_reasoning_body_renders_markdown_not_raw_pre():
    """思考正文必须 v-html 渲染 markdown，且渲染函数走 renderMarkdown。"""
    source = _source()
    assert 'class="reasoning-md-body" v-html="reasoningHtml(item)"' in source
    assert "import { renderMarkdown } from \"@/utils/markdown\";" in source
    # 思考分支原先那个裸 <pre> 必须消失。旁白与工具输出仍然用 <pre>（它们本就该保留原文），
    # 所以只针对思考那一段的类名特征断言。
    assert 'class="w-fit max-w-full whitespace-pre-wrap break-words pr-6 font-sans"' not in source
    # 限高容器与「展开全部」入口都要在思考分支里
    assert "reasoning-md-clamped" in source
    assert "展开全部（共" in source


def test_reasoning_clamp_threshold_and_pending_exemption():
    """限高阈值存在，且进行中与已展开的条目不被限高。"""
    source = _source()
    assert "const REASONING_CLAMP_CHAR_THRESHOLD = 2000;" in source
    clamp_fn = source[source.index("function isReasoningClamped"):]
    clamp_fn = clamp_fn[: clamp_fn.index("\n}")]
    assert 'if (item.textKind !== "reasoning") return false;' in clamp_fn
    # 进行中不限高：流式内容还在增长
    assert "if (item.pending) return false;" in clamp_fn
    # 用户点过「展开全部」后永久放开
    assert "reasoningFullExpanded.value.has(String(item.id))" in clamp_fn
    assert "reasoningCharCountOf(item) > REASONING_CLAMP_CHAR_THRESHOLD" in clamp_fn


def test_reasoning_expand_state_lives_in_component_not_timeline():
    """展开状态不能写回 item（时间线会被重建，写回的字段会被冲掉）。"""
    source = _source()
    expand_fn = source[source.index("function expandReasoningFull"):]
    expand_fn = expand_fn[: expand_fn.index("\n}")]
    assert "item.contentExpanded" not in expand_fn
    assert "reasoningFullExpanded.value = next" in expand_fn


def test_timeline_node_shapes_cover_four_kinds():
    """四类节点各有形状类，失败另有独立标记；委派判定与 processTimeline 口径一致。"""
    source = _source()
    assert 'return item.kind === "log" && isSubagentLog(item);' in source
    for marker in ('tl-node-think', 'tl-node-tool', 'tl-node-say', 'tl-node-agent', 'tl-node-fail'):
        assert marker in source, marker
    subagent_fn = source[source.index("function isSubagentLog"):]
    subagent_fn = subagent_fn[: subagent_fn.index("\n}")]
    for guard in ('startsWith("subagent_")', '"sub_agent_call"', '"sub_agent_batch_call"', "调用子代理", "委派智能体"):
        assert guard in subagent_fn, guard


def test_structural_colors_use_translucent_gray_not_theme_vars():
    """结构色用半透明中性灰：深浅两套主题一套值，也不引主题变量。"""
    style = _style_block()
    assert "rgba(127, 127, 127, 0.14)" in style  # 轨道竖线
    assert "rgba(127, 127, 127, 0.52)" in style  # 思考节点（完成态再由 opacity 压淡）
    # 上下文的主题变量一旦被 Tailwind 丢弃，var() 就会取不到值（颜色塌成继承）。
    assert "var(--rm-" not in style
    assert "var(--tl-" not in style
    # 契约禁止：会被编译成命中所有 .dark 元素的全局规则。
    # 注意别用 `.split("/*", 1)[0]` 截样式块：样式块一开头就是注释，那样截出来是空串，
    # 断言恒真。这里先整段去掉注释，再检查。
    style_block = re.sub(r"/\*.*?\*/", "", style.split("<style scoped>", 1)[1], flags=re.S)
    assert ":global(.dark)" not in style_block


#: 版式层面允许出现的饱和色，各有一条不可替代的理由。
#: - `copiedKey`：复制成功的一次性反馈，1 秒后消失，不构成常驻视觉；
#: - `本会话已授权`：语义徽标，绿色就是「这次没弹确认」的信息本身；
#: - `text-red-` / `amber`：失败与换模提示，必须被立刻看见；
#: - `rgb(239 68 68)`：轨道失败节点，同上。
_SATURATION_ALLOWLIST = ("copiedKey", "本会话已授权", "text-red-", "amber", "rgb(239 68 68)")


def test_thinking_surface_stays_neutral_grey():
    """思考展示区只允许灰阶，不许冒出新的饱和色。

    用户明确要求过「不要五颜六色，思考整体是浅浅的浅色风格」。这条测试的实际价值在于
    **防回潮**：改造过程中先后清掉过紫色技能徽章、indigo 嵌套线、sky 的挂起状态、
    indigo 的工具进度条——每清一处都有人觉得「就这一小块，留着吧」。
    """
    source = COMPONENT.read_text(encoding="utf-8")
    offenders = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        if any(allowed in line for allowed in _SATURATION_ALLOWLIST):
            continue
        for colour in ("purple", "indigo", "violet", "cyan", "sky-", "blue-", "teal"):
            if colour in line:
                offenders.append(f"{line_no}: {line.strip()[:90]}")
    assert offenders == [], f"链路里出现饱和色：{offenders}"


def test_skill_badge_is_neutral():
    """技能徽章曾用紫色，是链路容器里最跳的一块，现已中性化。"""
    source = COMPONENT.read_text(encoding="utf-8")
    badge_start = source.index("v-if=\"skillBadges.length\"")
    badge_block = source[badge_start: badge_start + 900]
    assert "purple" not in badge_block
    assert "border-gray-200" in badge_block
