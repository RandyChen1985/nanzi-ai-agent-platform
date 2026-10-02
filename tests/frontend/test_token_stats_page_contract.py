"""Token 统计页（`TokenStats.vue`）的取数与呈现契约。

## 背景

该页一次要取三份数据（趋势 / 智能体分布 / 用户账单），由顶部时段选择器驱动。
改前的实现有三处问题：

1. **串行取数**：三个接口逐个 `await`，总耗时是三段之和，而它们互不依赖；
2. **无竞态保护、失败静默**：连点时段时先发的请求可能后到、用旧时段覆盖新时段；
   `catch` 只打 `console.error`，界面上没有任何反馈；且**不清空数据**——留下的是
   上一个时段的结果，用户会当成当前时段的数据读；
3. **加载态复用空态**：`v-if="trendData.length === 0"` 既表示"还没加载"也表示
   "确实没有数据"，首屏于是把"正在请求"显示成"暂无数据"。

## 要求

1. 三个接口并行请求（`Promise.all`），且非管理员**不发起**用户账单请求。
2. 竞态版本号：过期响应必须丢弃，`loading` 只在最后一次请求上收口。
3. 失败必须清空三份数据并给出 toast——不能留下上一个时段的数据冒充当前时段。
4. 加载中与"暂无数据"必须是**不同分支**：不允许出现裸的
   `v-if="<data>.length === 0"` 空态（它会把加载中显示成没有数据）。
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOKEN_STATS = ROOT / "frontend/src/views/TokenStats.vue"


def _source() -> str:
    return TOKEN_STATS.read_text(encoding="utf-8")


def _normalized() -> str:
    # 归一化空白：契约约束行为，不应因重新格式化/换行而假失败
    return re.sub(r"\s+", " ", _source())


def _refresh_body() -> str:
    """refreshAll 的函数体（归一化后）。"""
    source = _normalized()
    body = source[source.index("const refreshAll = async") :]
    return body[: body.index("const trendChartOption")]


def test_three_endpoints_are_requested_in_parallel():
    """三个接口互不依赖，必须并行；串行 await 会让总耗时变成三段之和。"""
    body = _refresh_body()

    assert "await Promise.all([" in body, "三个统计接口应并行请求"
    # 逐个 await 的写法不应再出现
    for serial in (
        "const trendRes = await axios.get",
        "const agentRes = await axios.get",
        "const userRes = await axios.get",
    ):
        assert serial not in body, f"仍是串行请求：{serial}"


def test_user_billing_request_is_skipped_for_non_admins():
    """非管理员不该发起用户账单请求——既省一次往返，也避免无谓的 403。"""
    body = _refresh_body()

    assert 'const isAdmin = userInfo.value?.role === "admin";' in body
    assert ": Promise.resolve(null)," in body, "非管理员分支应短路成已决议的空请求"


def test_stale_responses_are_discarded():
    """连点时段时先发的请求可能后到，必须按版本号丢弃。"""
    body = _refresh_body()

    assert "const requestVersion = ++statsRequestVersion;" in body
    assert body.count("if (requestVersion !== statsRequestVersion) return;") == 2, (
        "成功分支与 catch 分支都要丢弃过期响应"
    )
    assert "if (requestVersion === statsRequestVersion) loading.value = false;" in body, (
        "loading 只能在最后一次请求上收口"
    )


def test_failure_clears_data_and_notifies_the_user():
    """失败必须清空 + toast：留着旧时段的数据会被读成当前时段的结果。"""
    body = _refresh_body()
    catch_at = body.index("} catch (error) {")
    catch_block = body[catch_at : body.index("} finally {")]

    for cleared in ("trendData.value = [];", "agentData.value = [];", "userData.value = [];"):
        assert cleared in catch_block, f"失败时应清空：{cleared}"
    assert "showToast(" in catch_block, "失败必须在界面上给反馈，不能只打 console"


def test_loading_state_is_distinct_from_empty_state():
    """加载中与"暂无数据"必须分开，否则首屏会把"正在请求"显示成"没有数据"。"""
    source = _source()

    # 每个加载中分支后面都必须紧跟同源的 else-if 空态分支——两者成对出现才算区分开。
    # （不能简单断言"没有裸空态"：`v-else-if="x.length === 0"` 正是配对后的正确写法。）
    for match in re.finditer(r'v-if="loading && (\w+)\.length === 0"', source):
        data = match.group(1)
        tail = source[match.end() : match.end() + 300]
        assert f'v-else-if="{data}.length === 0"' in tail, (
            f"{data} 的加载中分支后面没有跟同源的空态分支"
        )

    covered = set(re.findall(r'v-if="loading && (\w+)\.length === 0"', source))
    assert {"trendData", "agentData", "userData"} <= covered, (
        f"这些数据源还没有区分加载中与空态：{{'trendData','agentData','userData'}} - {covered}"
    )


def test_admin_only_panel_stays_gated_by_role():
    """用户账单面板仍只对管理员渲染（本次改动不应放开这个门槛）。"""
    source = _normalized()

    assert 'v-if="userInfo?.role === \'admin\'"' in source or "v-if=\"userInfo?.role === 'admin'\"" in source
