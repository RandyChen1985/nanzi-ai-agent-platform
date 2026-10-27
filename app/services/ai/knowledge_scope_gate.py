"""知识库检索范围门禁：开关判定、四道与门、卡片选项构造与回执解析。

本模块只做纯逻辑与数据组装：不访问数据库、不调用工具、不产生 SSE，
便于单测覆盖；副作用（读会话 scope、加载目录、出卡片）留在 runner 侧。
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# 卡片选项的稳定标记（不是 dataset_id，具体 ID 由 pending 快照还原）
SCOPE_RANKED = "__scope_ranked__"
SCOPE_ALL = "__scope_all__"
SCOPE_BOUND = "__scope_bound__"
SCOPE_PICK = "__scope_pick__"

DEFAULT_THRESHOLD = 5
DEFAULT_RANKED_LIMIT = 3

_SCOPE_OPTION_RE = re.compile(r"__scope_(?:ranked|all|bound|pick)__")

_CHOICE_MODES = {
    SCOPE_RANKED: "ranked",
    SCOPE_BOUND: "bound",
    SCOPE_ALL: "all",
    SCOPE_PICK: "picked",
}


@dataclass(frozen=True)
class ScopeGate:
    """一次门禁的完整快照：选项用于出卡片，ID 列表用于回答后还原检索范围。"""

    gate_id: str
    options: list[dict[str, Any]] = field(default_factory=list)
    ranked_ids: tuple[str, ...] = ()
    bound_ids: tuple[str, ...] = ()
    all_ids: tuple[str, ...] = ()
    total_count: int = 0
    bound_count: int = 0

    def to_pending_payload(self) -> dict[str, Any]:
        """写入会话 ``pending_scope_gate`` 的最小快照。"""
        return {
            "gate_id": self.gate_id,
            "ranked_ids": list(self.ranked_ids),
            "bound_ids": list(self.bound_ids),
            "all_ids": list(self.all_ids),
        }


def _clean_ids(values: Sequence[Any] | None) -> list[str]:
    ordered: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in ordered:
            ordered.append(text)
    return ordered


def build_scope_options(
    *,
    total_count: int,
    bound_count: int,
    ranked_names: Sequence[str] = (),
    ranked_count: int = 0,
) -> list[dict[str, Any]]:
    """构造四个卡片选项。

    ``disabled`` 是本模块的约定字段，出卡片前由调用方过滤——``ask_user_question``
    的选项 schema 没有禁用态，置灰无法表达，只能不展示。
    """
    if ranked_count > 0:
        preview = "、".join(list(ranked_names)[:3])
        ranked_label = (
            f"在 {ranked_count} 个最相关库中检索：{preview}" if preview else f"在 {ranked_count} 个最相关库中检索"
        )
        ranked_description = "按本次问题与知识库名称、描述的匹配度自动挑选，通常足够回答"
    else:
        ranked_label = "在 0 个最相关库中检索"
        ranked_description = "没有匹配到相关知识库，请改用其他方式指定范围"

    return [
        {
            "id": SCOPE_RANKED,
            "label": ranked_label,
            "description": ranked_description,
            "disabled": ranked_count == 0,
        },
        {
            "id": SCOPE_ALL,
            "label": f"全部 {total_count} 个库",
            "description": f"在全部 {total_count} 个库中联合检索，速度较慢，可能召回不相关片段",
            "disabled": False,
        },
        {
            "id": SCOPE_BOUND,
            "label": f"仅智能体默认（{bound_count} 个）" if bound_count else "仅智能体默认（未绑定）",
            "description": (
                "只检索该智能体配置的知识库范围"
                if bound_count
                else "该智能体没有绑定知识库，此项不可用"
            ),
            "disabled": bound_count == 0,
        },
        {
            "id": SCOPE_PICK,
            "label": "我自己指定（加知识库附件）",
            "description": "在输入框添加知识库附件即可精确指定；带附件提问时不会再有本提示",
            "disabled": False,
        },
    ]


async def evaluate_scope_gate(
    *,
    query: str,
    effective_ids: Sequence[Any] | None,
    bound_ids: Sequence[Any] | None,
    explicit_request_ids: Sequence[Any] | None,
    conversation_scope: Mapping[str, Any] | None,
    catalog: Any,
    enabled: bool = True,
    threshold: int = DEFAULT_THRESHOLD,
    ranked_limit: int = DEFAULT_RANKED_LIMIT,
) -> ScopeGate | None:
    """四道与门同时成立才返回 gate；任一不满足返回 None（放行现有行为）。

    四道与门：
    1. 开关开启（全局 + 智能体级，已由调用方合成到 ``enabled``）；
    2. 本轮没有显式选库；
    3. 会话内还没有确认过范围；
    4. 实际检索范围（``effective_ids``）超过阈值。
    """
    from app.services.ai.knowledge_catalog import rank_knowledge_catalog

    if not enabled:
        return None
    if _clean_ids(explicit_request_ids):
        return None

    scope_state = (conversation_scope or {}).get("knowledge_scope")
    if isinstance(scope_state, Mapping) and _clean_ids(scope_state.get("dataset_ids")):
        return None

    ids = _clean_ids(effective_ids)
    # 阈值下界夹到 1：配置页允许填 0，而 threshold=0 会让「只要有一个库」就弹卡，
    # 等于把这个功能变成每轮都问一句。
    if len(ids) <= max(int(threshold), 1):
        return None

    bound = _clean_ids(bound_ids)
    ranked = rank_knowledge_catalog(query, catalog, limit=ranked_limit)
    ranked_ids = tuple(dataset_id for dataset_id, _score in ranked)
    name_by_id = {
        str(getattr(item, "ragflow_dataset_id", "")): str(getattr(item, "name", "") or "")
        for item in (getattr(catalog, "items", ()) or ())
    }
    ranked_names = [
        name_by_id[dataset_id]
        for dataset_id in ranked_ids
        if name_by_id.get(dataset_id)
    ]

    return ScopeGate(
        gate_id=f"sg_{secrets.token_hex(4)}",
        options=build_scope_options(
            total_count=len(ids),
            bound_count=len(bound),
            ranked_names=ranked_names,
            ranked_count=len(ranked_ids),
        ),
        ranked_ids=ranked_ids,
        bound_ids=tuple(bound),
        all_ids=tuple(ids),
        total_count=len(ids),
        bound_count=len(bound),
    )


async def _as_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


async def _as_int(raw: Any, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


async def resolve_scope_gate_settings(
    engine_config: Mapping[str, Any] | None,
) -> tuple[bool, int, int]:
    """双层开关：全局管「能不能用」，智能体 ``engine_config.scope_gate`` 管「要不要用」。

    ``scope_gate`` 四态互斥：``inherit`` / ``on`` / ``off`` / ``bound_only``。
    其中 ``bound_only`` 同时把范围策略定为「只检索本智能体绑定的库」，
    因此不再需要向用户确认范围——范围已经由配置决定，问也没得选。

    返回 ``(enabled, threshold, ranked_limit)``。
    """
    from app.services.config_service import ConfigService

    global_enabled = await _as_bool(
        await ConfigService.get("knowledge_scope_gate_enabled"), True
    )
    threshold = await _as_int(
        await ConfigService.get("knowledge_scope_gate_threshold"), DEFAULT_THRESHOLD
    )
    ranked_limit = await _as_int(
        await ConfigService.get("knowledge_scope_gate_ranked_limit"), DEFAULT_RANKED_LIMIT
    )

    mode = str((engine_config or {}).get("scope_gate") or "inherit").strip().lower()
    if mode == "off":
        return False, threshold, ranked_limit
    if mode == "bound_only":
        # 「仅绑定库」与三个确认态互斥：范围已被配置钉死，没有可问的余地，
        # 绑定数超过阈值也不再弹卡（见 §4.5 范围策略）。
        return False, threshold, ranked_limit
    # 全站总闸优先于智能体级 on：出事故时运维必须能一键止血。
    # 原先 on 直接返回 True、完全无视全局开关，于是关掉总闸也关不掉任何
    # 配了「强制开启」的智能体——那它就不是总闸了。
    # 全局开着时，on 的含义是「本智能体不跟随后续灰度」，行为与 inherit 一致。
    if not global_enabled:
        return False, threshold, ranked_limit
    return True, threshold, ranked_limit


def parse_scope_choice(text: str | None) -> str | None:
    """从用户回答回执里取出 ``__scope_*`` 标记；没有则返回 None。"""
    if not text:
        return None
    match = _SCOPE_OPTION_RE.search(str(text))
    return match.group(0) if match else None


def resolve_dataset_ids_for_choice(
    choice: str | None,
    pending: Mapping[str, Any] | None,
) -> list[str]:
    """把选项标记还原成具体 dataset_id（通道 B：第三方客户端不回传 ID 时兜底）。"""
    if not choice or not isinstance(pending, Mapping):
        return []
    if choice == SCOPE_RANKED:
        return _clean_ids(pending.get("ranked_ids"))
    if choice == SCOPE_BOUND:
        return _clean_ids(pending.get("bound_ids"))
    if choice == SCOPE_ALL:
        return _clean_ids(pending.get("all_ids"))
    return []


def scope_mode_for_choice(choice: str | None) -> str:
    return _CHOICE_MODES.get(str(choice or ""), "picked")


def build_gate_question(gate: ScopeGate) -> str:
    """卡片正文：让用户一眼看出为什么被问。"""
    return (
        f"本轮可检索的知识库有 {gate.total_count} 个，先确认这次的检索范围："
    )


__all__ = [
    "SCOPE_ALL",
    "SCOPE_BOUND",
    "SCOPE_PICK",
    "SCOPE_RANKED",
    "ScopeGate",
    "build_gate_question",
    "build_scope_options",
    "evaluate_scope_gate",
    "parse_scope_choice",
    "resolve_dataset_ids_for_choice",
    "resolve_scope_gate_settings",
    "scope_mode_for_choice",
]
