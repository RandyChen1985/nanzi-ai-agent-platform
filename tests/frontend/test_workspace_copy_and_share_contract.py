"""契约：「复制并分享」复制成功之后，刷新列表用的是本文件自己的入口。

背景（线上问题，`fdae50b1` 引入）：`WorkspaceManage.vue` 的 `handleCopyAndShare`
里写了 `void fetchDirectory(currentPath.value)`，但 `fetchDirectory` 只存在于
`WorkspaceBrowserDrawer.vue`（1119 行），本文件负责刷新的函数叫 `load`。

后果不是「刷新没生效」这么轻——这行在 `try` 块内抛 `ReferenceError`，被下面的
`catch` 接住：文件其实**已经复制成功**，用户却看到「复制失败：fetchDirectory is
not defined」，而且后面的 `openShare()` 不再执行，分享弹窗根本不弹。

为什么这条测试能兜住：本仓库的 `npm run build` 带 `vue-tsc -b`，但 `dev.sh`、
`Dockerfile` 与 `_build_common.sh` 走的都是 `npx vite build`，类型检查被整体跳过；
验收时在仓库根目录跑 `vue-tsc --noEmit` 又是 project references（`files: []`），
什么都不检查却 exit 0。于是 TS2304（Cannot find name）没有任何一道门能拦住。
真正的门禁应该是 `cd frontend && vue-tsc -b`，这里只是补一张网。

第三条（`test_page_only_calls_functions_it_defines_or_imports`）才是根因防护：
它不针对 `fetchDirectory` 这一个名字，而是扫本页所有 `void xxx(` / `await xxx(`
调用，要求每个名字都能在本页的 const / function / import 具名 / 对象解构里找到来源。
以后任何人再从隔壁文件抄一个函数名过来，这条会直接红。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend" / "src" / "views" / "WorkspaceManage.vue"
DRAWER = ROOT / "frontend" / "src" / "components" / "embed" / "WorkspaceBrowserDrawer.vue"

# 浏览器/JS 全局函数：在本文件里查不到定义是正常的，不算漏定义。
GLOBAL_CALLEES = {
    "setTimeout",
    "clearTimeout",
    "setInterval",
    "clearInterval",
    "requestAnimationFrame",
    "cancelAnimationFrame",
    "parseInt",
    "parseFloat",
    "encodeURIComponent",
    "decodeURIComponent",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _local_names(source: str) -> set[str]:
    """本文件自己提供（定义 / 导入 / 解构）的标识符集合。"""
    names: set[str] = set()
    # const load = / let x = / var y =
    names |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", source))
    # function foo(
    names |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", source))
    # import { a, b as c } from
    for block in re.findall(r"import\s+(?:type\s+)?\{([^}]*)\}\s+from", source):
        for part in block.split(","):
            alias = part.strip().split(" as ")[-1].strip()
            if alias:
                names.add(alias)
    # const { handleOpenCanvas, ... } = useXxx()
    for block in re.findall(r"\b(?:const|let|var)\s*\{([^}]*)\}\s*=", source):
        for part in block.split(","):
            alias = part.strip().split(":")[-1].split("=")[0].strip()
            if alias:
                names.add(alias)
    return names


def _body_of(source: str, marker: str) -> str:
    """从 marker 开始截到下一个顶层 const（够用：本文件函数之间以空行 + const 分隔）。"""
    start = source.index(marker)
    rest = source[start:]
    nxt = rest.find("\nconst ", 10)
    return rest if nxt == -1 else rest[:nxt]


def test_page_does_not_call_fetch_directory():
    """`fetchDirectory` 属于抽屉，管理页引用它必然 ReferenceError。

    这条直接锁住回归现场：本页刷新列表只能走自己的 `load`。
    """
    page = _read(PAGE)
    # 只认调用形式，注释里提到这个名字不算违规
    assert re.search(r"\bfetchDirectory\s*\(", page) is None, (
        "WorkspaceManage.vue 不能调用 fetchDirectory——它只在 "
        "WorkspaceBrowserDrawer.vue 里定义，本页的刷新入口是 load"
    )


def test_drawer_still_owns_fetch_directory():
    """反向保障：不要为了让上一条变绿，去把抽屉里的 fetchDirectory 删掉或改名。

    抽屉里的搜索态刷新（`preserveSearch`）依赖这个函数，两条路径不能混为一谈。
    """
    drawer = _read(DRAWER)
    assert "const fetchDirectory = async (" in drawer
    assert "preserveSearch" in drawer


def test_copy_and_share_jumps_to_copy_then_opens_share():
    """复制成功后：先跳到副本所在目录（副本要看得见），再弹分享框。

    刷新目标必须由**副本路径**决定：副本落在本人工作区根（`target_dir` 未指定），
    而触发这个菜单项的场景恰恰是用户正在浏览公共目录——刷新当前目录永远看不到副本，
    原实现「刷新 currentPath」其实没有兑现注释里「让副本可见」的承诺。

    刷新必须是 `void load(`、不能 `await`：`load` 内部自带 try/catch，刷新失败只提示
    「读取目录失败」，await 到这里会被外层 catch 误报成「复制失败」。
    """
    body = _body_of(_read(PAGE), "const handleCopyAndShare = async (")
    assert "openShare(" in body

    refresh = [line for line in body.splitlines() if "void load(" in line]
    assert len(refresh) == 1, f"应当只有一处刷新调用，实际 {len(refresh)} 处"
    assert "created.path" in refresh[0], (
        "刷新目标必须由副本路径推算（必须出现 created.path），"
        f"当前是：{refresh[0].strip()}"
    )
    assert "currentPath.value" not in refresh[0], (
        "不能刷新当前目录——用户此刻在公共目录，副本不在那里"
    )

    # 顺序契约：先跳转再弹窗
    assert body.index("void load(") < body.index("openShare(")
    assert "await load(" not in body


def test_drawer_uses_the_same_jump_target():
    """抽屉里是同一段逻辑（这个 bug 就是从抽屉复制出去时落的），两处必须同口径。"""
    body = _body_of(_read(DRAWER), "const copyAndShare = async (")
    refresh = [line for line in body.splitlines() if "void fetchDirectory(" in line]
    assert len(refresh) == 1, f"应当只有一处刷新调用，实际 {len(refresh)} 处"
    assert "created.path" in refresh[0]
    assert "currentPath.value" not in refresh[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_parent_directory_behaviour_via_node():
    """`parentDirectoryOf` 真跑一遍：它算错就会跳到不存在的目录。"""
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "tests/frontend/workspace_path.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "工作区路径父目录测试全部通过" in proc.stdout


def test_page_only_calls_functions_it_defines_or_imports():
    """根因防护：本页每个 `void f(` / `await f(` 的 f 都必须有本地来源。

    这正是 `fetchDirectory` 那次事故的成因——从隔壁文件抄了一个函数名。
    """
    source = _read(PAGE)
    called = set(re.findall(r"\b(?:void|await)\s+([A-Za-z_$][\w$]*)\s*\(", source))
    unknown = sorted(called - _local_names(source) - GLOBAL_CALLEES)
    assert not unknown, (
        "WorkspaceManage.vue 调用了本文件未定义、也未导入的函数："
        f"{unknown}（同类错误曾导致「复制成功却提示复制失败」）"
    )
