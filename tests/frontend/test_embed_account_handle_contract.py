"""移动端「我的账号」把手：chat 页右侧进、账号页右侧出。

## 用户要的形态（三轮反馈后的定稿）

1. chat 页（/dashboard/chat）右侧边缘一条**与顶部把手同款的灰线把手**（无背景、无阴影，
   只剩一道灰线），点它打开「我的账号」；
2. 「我的账号」页里同一位置一条「‹」把手，点它收起、回到对话（**形态不跟 chat 页同款**）；
3. 都只在移动端；
4. 视觉要细、不能挡视线（chat 页那颗降到与顶部把手同款的一道灰线），但不能点不到
   ——两颗高度都是 44px，热区都靠 before 伪元素向左扩 44px（约 54×44）。

## 链路（第二轮重构后的定稿）

    Chat.vue 的把手 ──postMessage {type:'OPEN_ACCOUNT'}──▶ EmbedChat.vue
    EmbedChat.vue  ──router.push({name:'EmbedPersonal'})──▶ /embed/personal（iframe 内 SPA）
    EmbedPersonal.vue 的把手 ──router.back()──▶ 回到对话

**把手由 Chat.vue 渲染，不是 Dashboard**：谁持有 iframe，谁负责跟它通信。第一版放在
Dashboard、靠 window 自定义事件转发给 Chat.vue，多一跳就多一处会断的地方——实测
「点击没反应」。现在 Dashboard 只管顶栏收起，完全不掺和挂件内部路由。

**挂件要上报当前视图（EMBED_VIEW）**：chat 页把手与账号页把手的坐标完全重合，
如果 chat 页那颗在进入账号页后还留在上面，会把账号页那颗整个盖住——用户点到的永远是
上层那颗、而它此时无事可做，表现就是「点了没反应」。所以 EmbedChat 挂载时上报
`view: chat`、EmbedPersonal 挂载时上报 `view: personal`，Chat.vue 据此收起自己的把手。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _normalized(path: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", source)


def _tag_containing(path: str, marker: str) -> str:
    """取出包含 marker 的那个元素标签（从最近的 `<` 到其后第一个 `>`）。"""
    source = _normalized(path)
    at = source.index(marker)
    start = source.rindex("<", 0, at)
    return source[start : source.index(">", at)]


def test_chat_page_handle_lives_where_the_iframe_lives():
    source = _normalized("frontend/src/views/Chat.vue")
    tag = _tag_containing("frontend/src/views/Chat.vue", 'aria-label="我的账号"')

    # 与 Dashboard 顶部把手同款：10px 宽的区域里放一道 32×4 的灰线（不加图标/阴影/圆角）
    assert "h-11" in tag and "w-2.5" in tag
    # **不能有背景色**：白底压在正文上就是一条白边（用户截图反馈过）
    assert "bg-" not in tag, "把手带背景色会在正文上显出一条白边"
    assert "shadow" not in tag and "rounded" not in tag
    assert "before:-left-11" in tag and "before:w-11" in tag
    # 贴右边、垂直居中
    assert "fixed right-0" in tag and "top-1/2" in tag and "-mt-[22px]" in tag
    # 只在移动端
    assert "lg:hidden" in tag
    # 点击直接进城，不经 window 事件
    assert '@click="openEmbeddedAccount"' in source
    assert "postMessage({ type: 'OPEN_ACCOUNT' }, '*')" in source
    assert "nanzi:embed-open-account" not in source


def test_dashboard_no_longer_owns_the_account_handle():
    """把手不该留在 Dashboard：那里拿不到 iframe，只能靠事件转发（第一版因此点不动）。"""
    source = _normalized("frontend/src/views/Dashboard.vue")

    assert 'aria-label="我的账号"' not in source
    assert "openEmbeddedAccount" not in source
    assert "embedAccountBridge" not in source
    # 顶栏收起逻辑仍归它管
    assert "showDashboardHeader" in source


def test_handle_yields_to_the_account_page():
    """chat 页把手必须能收起来：否则它会盖住账号页那颗同坐标的「‹」。"""
    chat = _normalized("frontend/src/views/Chat.vue")
    tag = _tag_containing("frontend/src/views/Chat.vue", 'aria-label="我的账号"')

    assert "v-if=\"embedView === 'chat'\"" in tag
    assert "'personal' : 'chat'" in chat


def test_both_embed_views_report_themselves():
    """两侧各自上报当前视图，宿主才知道该收起哪颗把手。"""
    chat = _normalized("frontend/src/views/EmbedChat.vue")
    personal = _normalized("frontend/src/views/EmbedPersonal.vue")

    assert 'postMessageToHost({ type: "EMBED_VIEW", view: "chat" })' in chat
    assert 'postMessageToHost({ type: "EMBED_VIEW", view: "personal" })' in personal


def test_embedchat_handles_open_account_by_spa_navigation():
    source = _normalized("frontend/src/views/EmbedChat.vue")

    assert 'case "OPEN_ACCOUNT":' in source
    branch = source[source.index('case "OPEN_ACCOUNT":') : source.index('case "OPEN_ACCOUNT":') + 400]
    assert 'router.push({ name: "EmbedPersonal" })' in branch
    # 不能靠改 iframe src / location 跳转：那会重载挂件，对话上下文全丢
    assert "window.location" not in branch


def test_personal_page_handle_keeps_its_own_shape():
    """账号页的「‹」**不跟着** chat 页降级成灰线：它是唯一出口，要可辨认（用户明确要求保持现状）。"""
    tag = _tag_containing("frontend/src/views/EmbedPersonal.vue", 'aria-label="收起我的账号"')

    assert "h-11" in tag and "w-3" in tag
    assert "shadow-md" in tag and "rounded-l-lg" in tag
    assert "-mt-[22px] right-0 top-1/2" in tag
    # 收起动效走 translate-x，不能再叠 -translate-y-1/2
    assert "-translate-y-1/2" not in tag
    assert "translate-x-2" in tag
    # 与 chat 页把手同断点
    assert "lg:hidden" in tag
    assert "sm:hidden" not in tag
