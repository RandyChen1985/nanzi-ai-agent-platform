"""契约：「进行中的会话」角标在开始 / 结束时主动刷新。

用户提问（原文）：「什么时候更新激活状态呢， 发送按钮触发后就更新一次的吗？然后 ai 消息
结束了也会触发更新吗」→ 查证结论：**都不触发**，只有轮询（浮层关闭 15s / 打开 3s），
所以角标最多滞后 15 秒。用户选定方案 1+2，即开始与结束各补一次主动刷新。

实现选择：监听 `isProcessing` 翻转，而不是在各个散点插调用。
`isProcessing` 的 true/false 翻转本身就覆盖了发送、权限确认、固化报表、中止、错误等所有路径
（文件里有 5 处置 true、10 处置 false），一处 watch 即可，避免在十个收尾点重复埋雷。

延迟 500ms 是必要的：后端运行锁的建立/释放与前端状态不同步，立刻查可能拿到的还是旧值。
"""
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
WATCH = "watch(isProcessing, () => scheduleActiveRunsRefresh())"
DELAY = "delayMs = 500"
TIMER = "activeRunsRefreshTimer"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_it_refreshes_when_processing_flips():
    """一处 watch 覆盖「开始」与「结束」两个方向。"""
    assert WATCH in _source(), "没有监听 isProcessing 翻转来刷新角标"


def test_refresh_is_delayed_for_lock_propagation():
    """延迟 500ms：等后端运行锁建立/释放完成，否则会查到旧值。"""
    assert DELAY in _source(), "没有 500ms 延迟（后端锁状态可能还没落定）"


def test_repeated_schedules_are_coalesced():
    """连续翻转只保留最后一次调度，避免连点发送打出一串请求。

    只看调度函数体内——卸载清理里也有同一句 clearTimeout，放全文检查会被它满足。
    """
    source = _source()
    head = source.index("function scheduleActiveRunsRefresh")
    body = source[head : source.index("\n}\n", head)]
    assert f"if ({TIMER} !== null) clearTimeout({TIMER})" in body, "调度函数内没有去重"


def test_timer_handle_is_reset_after_firing():
    """回调里要把句柄置空：否则变量永远非 null，下次 clearTimeout 的是一个已完成的定时器。"""
    source = _source()
    head = source.index("function scheduleActiveRunsRefresh")
    body = source[head : source.index("\n}\n", head)]
    assert f"    {TIMER} = null;" in body, "回调里没有把定时器句柄置空"


def test_timer_is_cleared_on_unmount():
    """卸载后不能再触发刷新（否则会对已销毁的组件状态动手）。

    注意：文件里有 6 个 onUnmounted，必须定位带 stopActiveRunsWatch 的那个主卸载钩子。
    """
    source = _source()
    stop_at = source.index("\n  stopActiveRunsWatch();")  # 带换行锚定，避开 6 空格缩进的那处
    begin = source.rindex("onUnmounted(", 0, stop_at)
    assert f"clearTimeout({TIMER})" in source[begin:stop_at], "卸载时没有清理刷新定时器"


def test_it_still_calls_refresh_active_runs():
    """底层仍是同一个刷新入口。"""
    assert "void refreshActiveRuns()" in _source()


def test_the_sfc_compiles():
    """整个 SFC 必须能被 Vue 官方编译器解析（`vue-tsc` 不查模板标签平衡）。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/views/EmbedChat.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(["node", "-e", script], cwd=str(ROOT / "frontend"),
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"EmbedChat.vue 模板无法编译：\n{proc.stderr}"
