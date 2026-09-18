"""严格模式下，宿主尚未下发凭据时必须显示**中性等待态**，而非红色「登录状态已失效」。

## 背景

组件调试台（`WidgetDebugger.vue`）在挂载时就用 `?strict_token=1` 载入 iframe，且
**不带任何 token / ticket**：

    const connect = () => { iframeUrl.value = '/embed/chat?strict_token=1'; };

于是 `EmbedChat.vue` 侧必然走这条链：

1. 解析到 `strict_token=1` → `strictTokenValidation = true`
2. 无 URL 凭据 → `scheduleCookieOnlyInitialization()`
3. **250ms 握手窗口**（留给父页面 postMessage 握手，人类根本来不及点按钮）后 → `initChat()`
4. `validateToken()` —— strict 模式恰好**禁掉了 Cookie / sessionStorage 兜底**，
   而 `config.token` 为空 → 必然返回 false
5. `authFailureReason = "no_session"` → 渲染红色「登录状态已失效」

结果是：**用户什么都还没做，就先被告知"登录状态已失效"**，暗示凭据被用掉或过期。
这与调试台自己的说明「点击发送时将自动调用 /api/v1/embed/tickets 申请一次性短时
Ticket」互相矛盾，也确实让使用者误以为环境坏了。

## 要求

「尚未下发凭据」（等待中）与「下发后被拒」（真失败）语义完全不同，必须分开呈现：

1. 存在可响应的等待态标志，且其依赖的 `initConfigReceived` **必须是 ref**——
   普通 `let` 不会被 `computed` 追踪，等待态将永远不更新。
2. 等待态仅在**严格模式**下成立（生产环境的同源 Cookie 认证失败仍须如实报错）。
3. 等待态在中性分支渲染，且**优先于**红色失效遮罩（`v-else-if` 链中的顺序）。
4. 等待态文案点明"等待宿主下发 INIT_CONFIG"，且**不得**使用失效/过期一类措辞。
"""
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _normalized() -> str:
    # 归一化空白：契约约束行为，不应因重新格式化/换行而假失败
    return re.sub(r"\s+", " ", EMBED_CHAT.read_text(encoding="utf-8"))


def test_init_config_received_is_reactive():
    """等待态依赖它，必须是 ref，否则 computed 不会追踪、状态永远不更新。"""
    source = _normalized()

    assert "const initConfigReceived = ref(false);" in source
    assert "initConfigReceived = true;" not in source
    assert "initConfigReceived.value = true;" in source


def test_awaiting_state_is_scoped_to_strict_mode_only():
    """仅严格模式等待；生产环境的真实认证失败仍须显示错误。"""
    source = _normalized()

    assert "isAwaitingHostInitConfig" in source
    assert (
        "const isAwaitingHostInitConfig = computed( () => "
        "strictTokenValidation.value && (!initConfigReceived.value || "
        "hostInitInFlight.value) );"
    ) in source


def test_awaiting_state_covers_the_credential_exchange_window():
    """换票/校验在途期间必须维持等待态。

    收到 INIT_CONFIG 时会立刻置 `initConfigReceived`（用于取消 URL/Cookie 的待发计时器），
    但此刻凭据尚未落定、`hasPermission` 仍是初始化前那次必然失败的校验留下的 false。
    若等待态只依据 `initConfigReceived`，换票的网络往返期间就会闪出一帧红色「登录状态已失效」
    ——即"点发送 INIT_CONFIG 后瞬间闪红"的成因。
    """
    source = _normalized()

    assert "const hostInitInFlight = ref(false);" in source
    assert "hostInitInFlight.value = true;" in source
    assert "hostInitInFlight.value = false;" in source


def test_init_config_handler_always_releases_the_inflight_flag():
    """必须用 try/finally 释放：否则某条异常路径会让页面永远停在等待态。"""
    source = _normalized()

    assert (
        "hostInitInFlight.value = true; try { await applyHostInitConfig(data); } "
        "finally { hostInitInFlight.value = false; }"
    ) in source


def test_every_init_config_branch_settles_permission_before_releasing_inflight():
    """关键不变量：等待态关闭时 `hasPermission` 必须已落到最终值。

    `finally` 关闭 `hostInitInFlight` 之后，遮罩分支立刻按 `hasPermission` 求值。若某条
    分支在返回前没有显式设置 `hasPermission`，关闭等待态的瞬间就会渲染上一次的陈旧状态
    （成功分支漏设 → 闪红；失败分支漏设 → 闪出正常态）。

    这里用「逐 return 检查 + 精确计数」双重断言：逐 return 检查能发现某个分支完全没赋值，
    但它会把**同函数内更早的失败分支**留下的赋值误判为满足，因此必须再用计数锁住条数
    （3 条成功路径 + 3 条失败路径）。新增/合并分支时应同步更新本契约。
    """
    source = _normalized()
    body = source[source.index("const applyHostInitConfig = async") :]
    # 归一化后没有换行可依，用紧随其后的下一个顶层声明作为边界
    body = body[: body.index("const handlePostMessage")]

    assert body.count("hasPermission.value = true;") == 3, "成功路径必须各自显式置 true"
    assert body.count("hasPermission.value = false;") == 3, "失败路径必须各自显式置 false"

    settled = False
    for line in body.splitlines():
        if "hasPermission.value =" in line:
            settled = True
        if line.strip() == "return;":
            assert settled, f"分支在 return 前未设置 hasPermission：{line.strip()}"
            settled = False
    # 末尾的兜底分支（cookie 回落）同样必须设置
    assert settled, "applyHostInitConfig 末尾分支未设置 hasPermission"


def test_awaiting_placeholder_precedes_the_failure_overlay():
    """等待态必须是 v-if 首分支，红色遮罩降级为 v-else-if 才能被抢占。

    注意断言必须把**条件与那个块绑定**（同一开标签内），否则只改 `v-if` 表达式
    （例如令其恒假）时，仅检查 `data-testid` 存在性的契约会漏检——已实测过该漏洞。
    """
    source = _normalized()

    # 等待态的开标签必须同时带条件与 testid（`[^>]*` 限定在同一标签内）
    assert re.search(
        r'<div v-if="isAwaitingHostInitConfig"[^>]*data-testid="embed-awaiting-init"',
        source,
    ), "等待态分支的条件与容器未绑定在同一标签上"

    awaiting_at = source.index('data-testid="embed-awaiting-init"')
    no_permission_at = source.index('v-else-if="!hasPermission"')

    assert awaiting_at < no_permission_at, "等待态必须排在失效遮罩之前"
    # 原先的首分支不应再以 v-if 出现（否则两条分支互不相干，会同时渲染）
    assert 'v-if="!hasPermission"' not in source


def test_awaiting_copy_is_neutral_and_actionable():
    """文案要说明在等什么、以及无需用户干预即可自动进入。"""
    source = _normalized()

    awaiting = source[source.index('data-testid="embed-awaiting-init"') :]
    awaiting = awaiting[: awaiting.index("<!-- No Permission Overlay -->")]

    assert "等待宿主下发凭据" in awaiting
    assert "INIT_CONFIG" in awaiting
    assert "自动进入对话" in awaiting
    # 不得复用失效/过期一类措辞，否则又变成误导
    for misleading in ("登录状态已失效", "已过期", "凭证已失效", "无访问权限"):
        assert misleading not in awaiting


@pytest.mark.parametrize("token", ["text-red-500", "bg-red-50"])
def test_awaiting_placeholder_is_not_styled_as_an_error(token):
    """红色 = 出错了。等待态不该用红色，否则观感上仍是报错。"""
    source = _normalized()

    awaiting = source[source.index('data-testid="embed-awaiting-init"') :]
    awaiting = awaiting[: awaiting.index("<!-- No Permission Overlay -->")]

    assert token not in awaiting
    assert "text-blue-500" in awaiting
