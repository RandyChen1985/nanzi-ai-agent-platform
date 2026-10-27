"""首屏骨架必须撑到「会话确定 + 历史落地」，否则会闪一次欢迎页。

## 背景

`EmbedChat.vue` 的模板是「骨架 / 欢迎页 / 消息列表」三选一：

    <div v-if="isInitialLoading">骨架</div>
    <WelcomeDashboard v-else-if="messages.length === 0" />
    ...消息列表...

旧实现把骨架当成「鉴权中」指示器，`validateToken()` 一过就 `isInitialLoading.value = false`。
但「确定会话 → 拉首屏历史 → 还原流式草稿」这几步还在后面串行 await：这段空档里
`messages` 仍是空数组，`WelcomeDashboard` 必然先渲染出来，历史到达后再整块替换——
用户看到的就是「欢迎页闪一下，跳成消息列表」，完全没有 loading 的过渡感。

修复后骨架的语义是「当前线程还没准备好」：只有首屏历史与流式快照都落地才撤，
并由超时兜底防止请求挂死。切会话（清空 `messages` 后重拉历史）走同一条路径，
否则同样会闪欢迎页。

本契约锁住两件事：撤骨架的时机与出口不可回退，以及切会话路径必须一并遮挡。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

pytestmark = pytest.mark.no_infrastructure


def _normalized() -> str:
    # 归一化空白：契约约束行为，不应因重新格式化/换行而假失败
    return re.sub(r"\s+", " ", EMBED_CHAT.read_text(encoding="utf-8"))


def _init_chat_body() -> str:
    """`initChat` 函数体（到下一个顶层声明 `const fetchConversationHistory` 为止）。"""
    source = _normalized()
    start = source.index("const initChat = async")
    return source[start : source.index("const fetchConversationHistory = async")]


def _switch_conversation_body() -> str:
    """切换历史会话那一段（清空消息到历史重载）。"""
    source = _normalized()
    start = source.index("// Reset message list and history state")
    return source[start : source.index("const handleDeleteHistory = async")]


def test_init_chat_opens_skeleton_before_auth():
    """骨架必须在鉴权之前立起来，否则鉴权往返期间会先露出欢迎页。"""
    body = _init_chat_body()

    assert "const skeletonToken = beginThreadSkeleton();" in body
    # 令牌声明必须早于 try：失败分支要用同一个令牌语义收尾
    assert body.index("const skeletonToken = beginThreadSkeleton();") < body.index("try {")


def test_init_chat_never_drops_the_skeleton_on_auth_success():
    """回归锁：鉴权通过后不得再直接撤骨架。

    这正是「欢迎页闪一下」的成因——此时 `messages` 还是空的，撤掉骨架就只剩欢迎页。
    `initChat` 内不允许出现裸的 `isInitialLoading.value = false`，撤骨架只能走
    `finishThreadSkeleton` / `abortThreadSkeleton` 两个受控出口。
    """
    body = _init_chat_body()

    assert "isInitialLoading.value = false" not in body
    assert "isInitialLoading.value = true" not in body


def test_skeleton_is_released_only_after_history_and_snapshot():
    """撤骨架必须排在历史与流式快照都落地之后。"""
    body = _init_chat_body()

    fetch_at = body.index("void fetchConversationHistory(false, initGeneration)")
    snapshot_at = body.index(".then(() => restoreEmbedStreamSnapshot(initGeneration))")
    release = body.index(".finally(() => finishThreadSkeleton(skeletonToken));")

    assert fetch_at < snapshot_at < release, "骨架必须在历史与快照都落地后才撤"
    # 还原快照失败（或被取消）也不能把骨架留在界面上
    assert '.catch(e => console.error("[Init] History load failed:", e))' in body


def test_init_chat_does_not_await_history():
    """`initChat` 不能等历史：宿主 INIT_CONFIG 是 `await initChat(...)`。

    等待会把「正在获取登录信息」遮罩一起拖到历史落地，而遮罩压在骨架之上——
    用户看不到刚立起来的骨架动效，观感仍是「遮罩撤掉瞬间内容已就位」的跳变。
    历史必须在链尾收尾，而不是拖住初始化握手。
    """
    body = _init_chat_body()

    assert "await fetchConversationHistory" not in body
    assert "await restoreEmbedStreamSnapshot" not in body


def test_failure_paths_abort_the_skeleton():
    """鉴权失败、agent 锁定失败、异常都必须撤骨架，否则页面停在骨架屏。"""
    body = _init_chat_body()

    assert body.count("abortThreadSkeleton();") == 3, "失败/异常分支必须各自无条件撤骨架"
    # catch 分支不能只在控制台打日志
    catch_tail = body[body.index("} catch (e) { console.error(\"Init chat failed\", e);") :]
    assert "abortThreadSkeleton();" in catch_tail, "异常分支必须撤骨架"


def test_switching_conversation_forces_the_same_skeleton():
    """切会话是同一根因：清空消息后到历史落地之间也会闪欢迎页。"""
    body = _switch_conversation_body()

    clear_at = body.index("messages.value = [];")
    begin_at = body.index("const switchSkeletonToken = beginThreadSkeleton({ force: true });")
    fetch_at = body.index("void fetchConversationHistory(false)")

    assert clear_at < begin_at < fetch_at, "骨架要在清空之后、拉历史之前强制立起来"
    assert "finishThreadSkeleton(switchSkeletonToken)" in body, "切会话的历史落地后必须撤骨架"
    # 历史请求失败同样要撤，否则一直停在骨架
    assert ".catch(" in body[fetch_at : fetch_at + 400]


def test_history_indicator_is_hidden_behind_the_skeleton():
    """两套 loading 不能叠加：骨架期间那个小胶囊由骨架统一代替。"""
    source = _normalized()

    assert 'v-if="isLoadingHistory && !isInitialLoading"' in source


def test_skeleton_has_a_timeout_fallback():
    """历史/快照挂死时不能永久停在骨架。"""
    source = _normalized()

    assert "FIRST_SCREEN_SKELETON_TIMEOUT_MS" in source
    assert "shouldStartThreadSkeleton" in source
    begin = source[source.index("const beginThreadSkeleton = ") :]
    begin = begin[: begin.index("const finishThreadSkeleton = ")]
    assert "setTimeout(" in begin, "骨架必须带超时兜底"
    assert "FIRST_SCREEN_SKELETON_TIMEOUT_MS" in begin


def test_skeleton_release_waits_for_inflight_history():
    """历史仍在飞时撤骨架会露出欢迎页：先挂起，等历史 finally 再撤。"""
    source = _normalized()

    finish = source[source.index("const finishThreadSkeleton = ") :]
    finish = finish[: finish.index("const abortThreadSkeleton = ")]
    assert "if (isLoadingHistory.value) {" in finish
    assert "pendingThreadSkeletonReleaseToken = token;" in finish

    history_finally = source[
        source.index("pendingThreadSkeletonReleaseToken && !isLoadingHistory.value") - 200 :
    ]
    assert "finishThreadSkeleton(pendingThreadSkeletonReleaseToken);" in history_finally[
        :400
    ], "历史落地后必须补上被挂起的骨架释放"


def test_skeleton_respects_a_minimum_visible_window():
    """请求太快时骨架只出现一两帧，观感上仍是「闪一下」：必须补足最短可见时长。"""
    source = _normalized()

    begin = source[source.index("const beginThreadSkeleton = ") :]
    begin = begin[: begin.index("const finishThreadSkeleton = ")]
    assert "threadSkeletonShownAt = Date.now();" in begin, "必须记录骨架出现时刻"

    finish = source[source.index("const finishThreadSkeleton = ") :]
    finish = finish[: finish.index("const abortThreadSkeleton = ")]
    assert "threadSkeletonRemainingVisibleMs(Date.now() - threadSkeletonShownAt)" in finish
    assert "if (remaining > 0) {" in finish, "不足最短时长时必须延后撤除"


def test_skeleton_timer_is_cleared_on_unmount():
    """兜底定时器必须随组件清理：卸载后它只会改一个没人看的 ref。"""
    source = _normalized()

    unmount = source[source.index("onUnmounted(() => { if (activeRunsRefreshTimer !== null)") :]
    assert "clearThreadSkeletonTimer();" in unmount[:400]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行骨架闸门行为测试")
def test_skeleton_gate_behaviour_via_node():
    script = Path("tests/frontend/embed_first_screen_skeleton.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "首屏骨架闸门测试全部通过" in proc.stdout
