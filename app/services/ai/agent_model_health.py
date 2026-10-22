"""智能体模型健康检查：判断某个智能体绑定的 LLM 模型在当前环境是否可用。

现场故障里，智能体版本存了一个目标环境并不存在的模型名，界面上完全看不出来，
只有运行时才报 ``LLM API Key is missing for model 'xxx'``，把「模型没配」误诊成
「密钥问题」。这里把判定收敛成纯函数，供列表接口在渲染时顺带计算。

判定只覆盖 LOCAL 引擎：OPENCLAW 的 ``model`` 是机器人 ID，RAGFLOW 不调用平台
LLM，两者都不该拿模型表去校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# 展示给用户的短标签，前端也可直接使用。
MODEL_ISSUE_LABELS = {
    "missing": "未配置模型",
    "unregistered": "模型未注册",
    "disabled": "模型已禁用",
    "missing_api_key": "未配置 API Key",
}


@dataclass(frozen=True)
class ModelIssue:
    code: str
    message: str
    model_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "model_name": self.model_name,
        }


@dataclass(frozen=True)
class _Entry:
    model_id: str
    name: str
    is_active: bool
    has_api_key: bool


class ModelRegistrySnapshot:
    """一次载入的模型表快照，避免逐智能体查库。"""

    def __init__(self, entries: Iterable[_Entry]):
        self._by_id: dict[str, _Entry] = {}
        self._by_name: dict[str, list[_Entry]] = {}
        for entry in entries:
            self._by_id.setdefault(entry.model_id, entry)
            if entry.name:
                self._by_name.setdefault(entry.name, []).append(entry)

    @classmethod
    def from_rows(cls, rows: Iterable[Any]) -> "ModelRegistrySnapshot":
        entries: list[_Entry] = []
        for row in rows:
            model_id = str(getattr(row, "model_id", "") or "").strip()
            if not model_id:
                continue
            entries.append(
                _Entry(
                    model_id=model_id,
                    name=str(getattr(row, "name", "") or "").strip(),
                    is_active=bool(getattr(row, "is_active", True)),
                    has_api_key=bool(str(getattr(row, "api_key", "") or "").strip()),
                )
            )
        return cls(entries)

    def resolve(self, model_name: str) -> tuple[Optional[_Entry], bool]:
        """按 model_id 精确匹配，再退到唯一的显示名。

        返回 ``(命中记录, 是否名称歧义)``。显示名命中多条时不猜，交由调用方
        提示改用 model_id——与 ``model_registry.resolve_model_record`` 的口径一致。
        """
        name = model_name.strip()
        exact = self._by_id.get(name)
        if exact is not None:
            return exact, False
        named = self._by_name.get(name) or []
        if len(named) > 1:
            return None, True
        return (named[0] if named else None), False


def evaluate_model_issue(
    model_name: Optional[str],
    *,
    registry: ModelRegistrySnapshot,
    has_global_api_key: bool,
) -> Optional[ModelIssue]:
    """返回该模型名对应的配置问题；完全可用时返回 None。"""
    name = str(model_name or "").strip()
    if not name:
        return ModelIssue("missing", MODEL_ISSUE_LABELS["missing"], "")

    entry, ambiguous = registry.resolve(name)
    if ambiguous:
        return ModelIssue(
            "unregistered",
            f"模型名称存在多个同名记录，无法确定使用哪一个：{name}",
            name,
        )
    if entry is None:
        return ModelIssue(
            "unregistered",
            f"模型未在当前环境的模型管理中注册：{name}",
            name,
        )
    if not entry.is_active:
        return ModelIssue(
            "disabled",
            f"{MODEL_ISSUE_LABELS['disabled']}：{name}",
            name,
        )
    # 模型自身没配 key 时仍可回落到全局 llm_api_key，两者都空才是真的没有凭据。
    if not entry.has_api_key and not has_global_api_key:
        return ModelIssue(
            "missing_api_key",
            f"模型与全局回落配置都没有 API Key：{name}",
            name,
        )
    return None


async def load_model_registry(session: Any) -> ModelRegistrySnapshot:
    """一次读出模型表，供整页列表复用。"""
    from sqlalchemy import select

    from app.models.ai_model import AIModel

    rows = (await session.execute(select(AIModel))).scalars().all()
    return ModelRegistrySnapshot.from_rows(rows)


async def load_global_llm_api_key() -> bool:
    """全局回落凭据是否存在（模型记录缺 key 时运行时用的就是它）。"""
    from app.services.config_service import ConfigService

    return bool(str(await ConfigService.get("llm_api_key") or "").strip())


async def load_default_model_issue(session: Any) -> Optional[ModelIssue]:
    """系统默认 LLM 模型本身是否可用。

    默认模型未配置或指向不存在的模型时，所有「没有显式绑定模型」的链路都会在
    运行时失败，但界面上看不出任何异常——所以管理页顶部也要提示这一条。
    """
    from app.services.ai.default_model import get_default_llm_model

    registry = await load_model_registry(session)
    has_global_api_key = await load_global_llm_api_key()
    model_name = await get_default_llm_model()
    return evaluate_model_issue(
        model_name,
        registry=registry,
        has_global_api_key=has_global_api_key,
    )
