"""业务分组名的归一口径（单值）。

平台里有四处「业务分组」，语义一致、口径也必须一致：
工具注册表（`sys_api_tools` / `sys_script_tools`）、元数据管理（`meta_datasets`）、
智能体中心（`ai_agents`），以及技能工作台（写进 `SKILL.md` 的 `group:`）。

四个页面的页签是同一套组件、同一套计数规则，所以「什么算没分组」只能有一个答案：
去首尾空白，空白串一律视为未分组（`None`）。否则 `"   "` 会变成页签上一个看不见的空分组，
点进去还只有它自己。
"""

from __future__ import annotations

from typing import Any, Optional

#: 分组名长度上限。与 `meta_datasets.group_name` / `ai_agents.group_name` 的
#: VARCHAR(64) 对齐——超出会在落库时被截断（MySQL 严格模式则直接报错），
#: 所以在入参阶段就归一，别等数据库来兜。
GROUP_NAME_MAX_LEN = 64


def normalize_group_name(value: Optional[Any]) -> Optional[str]:
    """业务分组名归一：去首尾空白，空白串视为「未分组」（None）。

    既允许使用者清空分组（回到未分组），也避免把 ``"   "`` 存成看不见的空分组。
    换行会折成空格：前端输入框理论上进不来换行，但接口可能被直接调用，
    而换行会让「一行一个分组」的展示与导出错位。
    """
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:GROUP_NAME_MAX_LEN]
