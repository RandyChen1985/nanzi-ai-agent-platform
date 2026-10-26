"""平台内置知识库的识别规则。

有些 RAGFlow 库不是用户建的，是平台自己生成/维护的：

- ``chatbi-example-meta``：ChatBI 优质案例的元数据库，由
  ``ChatbiExampleService`` 在初始化时自动建；
- ``meta-*``：元数据管理侧的保留命名。

它们既不该出现在「发现」（用户申请不到也没意义），也不该出现在知识库
管理列表里（用户会以为是自己的库，删了还会被重建）。规则统一放在这里，
避免每个列表入口各写一份、改一处漏一处。
"""

from typing import Optional

#: 名字里带这个片段的都算内置案例库（含 ``chatbi-example-meta``）
_INTERNAL_NAME_FRAGMENTS = ("chatbi-example",)
#: 保留前缀
_INTERNAL_NAME_PREFIXES = ("meta-",)


def is_internal_dataset_name(name: Optional[str]) -> bool:
    """是否为平台内置知识库。空名字也按内置处理——它没法展示也没法申请。"""
    normalized = str(name or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith(_INTERNAL_NAME_PREFIXES):
        return True
    return any(fragment in normalized for fragment in _INTERNAL_NAME_FRAGMENTS)
