"""技能链路不得再退回浏览器原生弹窗。

原生 `prompt` / `alert` / `confirm` 会阻塞主线程、样式无法跟主题一致，在内嵌 WebView
里还可能被直接拦截；`window.prompt` 更严重——输入框无法校验、无法显示错误、无法标记
加载态，用户重命名技能时看到的是一句裸提示，与页面其余部分完全脱节。

本次只收口技能链路（用户实际反馈的入口）。全站其余原生弹窗是既有存量，登记在
`_PENDING_NATIVE_DIALOGS` 里守住「只减不增」，不在本轮一并重写。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"

#: 技能链路：这些文件里出现原生弹窗即为回归
_SKILL_CHAIN = [
    "views/SkillsManagement.vue",
    "components/InputModal.vue",
]

#: 已清零：全站原生弹窗全部收敛完毕，保持为空即为「零容忍」（只减不增）。
_PENDING_NATIVE_DIALOGS: set[str] = set()

#: 原生弹窗的两种调用形态：显式 `window.xxx(` 与裸 `xxx(`。
#: 反查断言不能写成 `(?<![\w.])` —— 那会把最常见、也最该拦的 `window.prompt(` 本身
#: 排除掉（`prompt` 前面正是 `.`），反向验证时踩过这个坑。
_NATIVE = re.compile(
    r"\bwindow\s*\.\s*(?:prompt|alert|confirm)\s*\("
    r"|(?<![\w.$])(?:prompt|alert|confirm)\s*\("
)

#: 项目自建确认框的调用与定义形态：`await confirm({...})` / `function confirm(...)`。
#: 它和原生 `confirm("...")` 在字面上都是 `confirm(`，正则分不出来——但**来源**能分：
#: 文件里出现了 `useConfirm`，说明这个 `confirm` 是 from '@/composables/useConfirm'
#: 拿到的，恰恰是这条棘轮鼓励的方向。真正的原生调用仍被 `window.confirm(` 那条硬拦。
_OWN_CONFIRM = re.compile(r"(?<![\w.$])confirm\s*\(")
_WINDOW_CONFIRM = re.compile(r"\bwindow\s*\.\s*confirm\s*\(")


def _native_dialog_sites(path: Path) -> list[str]:
    """返回文件中真正调用原生弹窗的 `相对路径:行号`（注释与文档字符串不算）。"""
    sites = []
    in_block_comment = False
    text = path.read_text(encoding="utf-8")
    # 引入过自建确认框的文件，裸 `confirm(` 是它的调用/定义，不是原生弹窗
    uses_own_confirm = "useConfirm" in text
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        if stripped.startswith(("//", "*")):
            continue
        if _NATIVE.search(line):
            if (
                uses_own_confirm
                and not _WINDOW_CONFIRM.search(line)
                and _OWN_CONFIRM.search(line)
            ):
                continue
            sites.append(f"{path.relative_to(FRONTEND)}:{number}")
    return sites


def test_skill_chain_has_no_native_dialogs():
    """技能管理页与输入弹窗组件都必须使用自建弹窗。"""
    offenders = []
    for rel in _SKILL_CHAIN:
        offenders.extend(_native_dialog_sites(FRONTEND / rel))
    assert not offenders, (
        "技能链路又出现了原生弹窗，请改用 InputModal / ConfirmModal: "
        + ", ".join(offenders)
    )


def test_rename_uses_input_modal():
    """重命名必须走 InputModal，且不能再有 window.prompt 的调用形态。"""
    text = (FRONTEND / "views/SkillsManagement.vue").read_text(encoding="utf-8")
    assert "InputModal" in text, "技能管理页未接入 InputModal"
    assert "<InputModal" in text, "InputModal 只导入未挂载"
    assert ":loading=\"renameSubmitting\"" in text, (
        "重命名提交态未接给弹窗，用户会以为卡住"
    )
    assert "@confirm=\"submitRename\"" in text, "弹窗确认事件未接上重命名逻辑"


def test_no_new_native_dialogs_elsewhere():
    """全站其余原生弹窗只减不增。"""
    current = set()
    for path in FRONTEND.rglob("*.vue"):
        current.update(_native_dialog_sites(path))
    for path in FRONTEND.rglob("*.ts"):
        current.update(_native_dialog_sites(path))

    added = sorted(current - _PENDING_NATIVE_DIALOGS)
    assert not added, (
        "新增了原生弹窗，请改用页面内的 Toast/弹窗组件；若确需改造存量请同步更新清单:\n"
        + "\n".join(added)
    )


def test_pending_list_has_no_stale_entries():
    """修掉的存量要从清单里删掉，否则棘轮会失去意义。"""
    current = set()
    for path in FRONTEND.rglob("*.vue"):
        current.update(_native_dialog_sites(path))
    for path in FRONTEND.rglob("*.ts"):
        current.update(_native_dialog_sites(path))

    stale = sorted(_PENDING_NATIVE_DIALOGS - current)
    assert not stale, "清单里这些已不存在，请删除对应条目:\n" + "\n".join(stale)
