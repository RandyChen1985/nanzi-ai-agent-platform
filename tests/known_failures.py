"""已知失败基线的解析与校验。

把"长期存在的失败"与"本次改动引入的回归"区分开的机制核心：conftest 只做接线，
真正的解析、校验、分类规则集中在这里，便于直接单测。

清单格式（每行一条）：

    <nodeid>  # <分类>: <说明>

分类前缀必须取自 CATEGORIES 之一——禁止裸加条目，否则基线会变成垃圾桶。
"""

from pathlib import Path

import pytest

DEFAULT_PATH = Path(__file__).parent / "known_failures.txt"

# 允许的分类前缀；每条基线必须归到其中一类。
CATEGORIES = (
    "A-断言过时",
    "B-依赖升级适配",
    "C-环境与配置",
    "D-测试状态污染",
    "E-疑似真实缺陷",
)

# nodeid 与原因之间的分隔符（两个空格 + #），刻意与行首注释区分开。
_SEPARATOR = "  # "


def load_known_failures(path: Path | None = None) -> dict[str, str]:
    """读取基线清单，返回 ``{nodeid: 原因}``。

    空行与以 ``#`` 开头的整行注释会被忽略；格式非法时抛 ``ValueError``——
    宁可让机制报错，也不接受一条没有分类的裸条目。
    """
    path = path or DEFAULT_PATH
    if not path.exists():
        return {}

    known: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _SEPARATOR not in line:
            raise ValueError(
                f"{path}:{lineno} 缺少原因（格式：<nodeid>  # <分类>: <说明>）"
            )
        nodeid, reason = line.split(_SEPARATOR, 1)
        nodeid, reason = nodeid.strip(), reason.strip()
        if "::" not in nodeid:
            raise ValueError(
                f"{path}:{lineno} 条目不像 nodeid（缺少 ::）：{nodeid}"
            )
        if any(ch.isspace() for ch in nodeid):
            raise ValueError(
                f"{path}:{lineno} nodeid 含空白，多半是生成时混入了 pytest "
                f"输出的告警文本：{nodeid}"
            )
        if not any(reason.startswith(category) for category in CATEGORIES):
            raise ValueError(
                f"{path}:{lineno} 分类前缀非法，必须以下列之一开头 "
                f"{'、'.join(CATEGORIES)}：{reason}"
            )
        if nodeid in known:
            raise ValueError(f"{path}:{lineno} 重复条目：{nodeid}")
        known[nodeid] = reason
    return known


def missing_from_collection(known: dict[str, str], collected: set[str]) -> list[str]:
    """返回清单里存在、但本次收集中已不存在的 nodeid。

    这类条目通常是拼写错误，或对应的测试已被删除/重命名——正是
    ``--strict-known-failures`` 要提醒清理的东西。
    """
    return sorted(nodeid for nodeid in known if nodeid not in collected)


def mark_known_failures(items, known: dict[str, str], *, strict: bool) -> int:
    """给命中清单的用例打上 ``xfail`` 标记，返回命中数量。

    ``strict=False``（默认）时用例照常运行：修好了会显示成 XPASS 但不报错，
    方便观察治理进度；``strict=True``（``--strict-known-failures``）时已修好的
    条目会让运行失败，用来督促把它从清单里删掉。

    未列入清单的用例**绝不**打标记——它们的失败就是我们要看见的回归信号。
    """
    marked = 0
    for item in items:
        reason = known.get(item.nodeid)
        if reason is None:
            continue
        item.add_marker(pytest.mark.xfail(reason=reason, strict=strict))
        marked += 1
    return marked
