"""凭据定位规则：把「这次调用的入参」翻译成「要找的 secret_key」。

**纯函数，不查库、不解密。** 运行期解析（``resolver._locate``）与配置页试算
（``POST /secrets/locate-preview``）走的是同一段逻辑，否则「试算说能命中、
真跑却失败」这类问题永远查不清。

规则描述的是**怎么找**，不是**找到谁**：``parse_rule`` 会拒绝任何 ``secret_id``，
所以改一条规则永远变不成「读别人凭据」的手段。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: 身份类键来源（补充能力，主场景是 ``arg:<入参名>``）
USER_KEY_SOURCES = (
    "user.email",
    "user.user_name",
    "user.real_name",
    "user.dept_code",
    "user.user_id",
)
TRANSFORMS = ("trim", "strip_domain", "lower")
TARGETS = ("secret_key", "name", "category")
MATCHES = ("exact", "exact_ci")
ON_MISS = ("fail", "fallback_fixed", "model")
MAX_TEMPLATES = 3
ALLOWED_FIELDS = {"key_source", "transforms", "templates", "target", "match", "on_miss"}


class RuleError(ValueError):
    """规则不合法。``str(exc)`` 直接展示给配置者，所以要写人话。"""


@dataclass(frozen=True)
class RuleSpec:
    key_source: str
    transforms: tuple[str, ...] = ()
    templates: tuple[str, ...] = ("{value}",)
    target: str = "secret_key"
    #: 默认**不区分大小写**。原因：`secret_key` 在创建时被规范化为小写
    #: （只允许 ``[a-z0-9._-]``），而用户配的入参值往往带大写（``A``/``CC``）。
    #: 默认 ``exact`` 的话，「凭据 key 写 A、入参传 A」这种最常见的场景会匹配不上，
    #: 而界面上看不出任何问题——这是必须踩一次的坑。
    match: str = "exact_ci"
    on_miss: str = "fail"

    @property
    def is_arg_key(self) -> bool:
        return self.key_source.startswith("arg:")

    @property
    def arg_name(self) -> str | None:
        if not self.is_arg_key:
            return None
        return self.key_source[4:] or None


def _as_mapping(raw: str | bytes | Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "" or raw == b"":
        raise RuleError("规则为空")
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuleError(f"规则不是合法 JSON：{exc}") from exc
    else:
        data = dict(raw)
    if not isinstance(data, dict):
        raise RuleError("规则必须是一个 JSON 对象")
    return data


def parse_rule(raw: str | bytes | Mapping[str, Any] | None) -> RuleSpec:
    """校验并归一化规则。任何不合法的写法都在保存时就被拒绝。"""
    data = _as_mapping(raw)

    unknown = set(data) - ALLOWED_FIELDS
    if unknown:
        # secret_id 落在这里：规则不允许指定具体凭据
        raise RuleError(f"未知字段：{', '.join(sorted(unknown))}")

    key_source = str(data.get("key_source") or "").strip()
    if not key_source:
        raise RuleError("缺少 key_source（用哪个值去匹配）")
    if key_source.startswith("arg:"):
        if not key_source[4:].strip():
            raise RuleError("key_source 写成 arg: 时必须给出参数名，例如 arg:to")
    elif key_source not in USER_KEY_SOURCES:
        raise RuleError(
            f"key_source 不支持 {key_source!r}；可用 arg:<参数名> 或 "
            + " / ".join(USER_KEY_SOURCES)
        )

    transforms = data.get("transforms") or []
    if not isinstance(transforms, (list, tuple)):
        raise RuleError("transforms 必须是数组")
    bad = [t for t in transforms if t not in TRANSFORMS]
    if bad:
        raise RuleError(f"未知变换 {bad[0]!r}；可用 {' / '.join(TRANSFORMS)}")
    if len(set(transforms)) != len(transforms):
        raise RuleError("transforms 有重复项")

    templates = data.get("templates") or ["{value}"]
    if not isinstance(templates, (list, tuple)) or not templates:
        raise RuleError("templates 必须是非空数组")
    if len(templates) > MAX_TEMPLATES:
        raise RuleError(f"templates 最多 3 条，当前 {len(templates)} 条")
    for tpl in templates:
        if not isinstance(tpl, str) or "{value}" not in tpl:
            raise RuleError("每条 template 都必须包含 {value} 占位符")

    target = data.get("target") or "secret_key"
    if target not in TARGETS:
        raise RuleError(f"target 不支持 {target!r}；可用 {' / '.join(TARGETS)}")

    # 见 RuleSpec.match 的说明：默认不区分大小写
    match = data.get("match") or "exact_ci"
    if match not in MATCHES:
        raise RuleError(f"match 不支持 {match!r}；可用 {' / '.join(MATCHES)}")

    on_miss = data.get("on_miss") or "fail"
    if on_miss not in ON_MISS:
        raise RuleError(f"on_miss 不支持 {on_miss!r}；可用 {' / '.join(ON_MISS)}")

    return RuleSpec(
        key_source=key_source,
        transforms=tuple(transforms),
        templates=tuple(templates),
        target=target,
        match=match,
        on_miss=on_miss,
    )


def _apply_transforms(value: str, transforms: Sequence[str]) -> str:
    for name in transforms:
        if name == "trim":
            value = value.strip()
        elif name == "lower":
            value = value.lower()
        elif name == "strip_domain":
            # 不含 @ 时是空操作：同一套规则可以挂在邮箱键与其他键上
            value = value.split("@", 1)[0]
    return value


def build_candidates(
    spec: RuleSpec,
    *,
    identity: Mapping[str, Any] | None = None,
    args: Mapping[str, Any] | None = None,
) -> list[str]:
    """算出「按顺序要去找的那几个名字」。取不到值返回空列表（→ 未命中）。"""
    identity = identity or {}
    args = args or {}
    if spec.is_arg_key:
        raw = args.get(spec.arg_name) if spec.arg_name else None
    else:
        raw = identity.get(spec.key_source.partition(".")[2])
    if raw is None or raw == "":
        return []
    value = _apply_transforms(str(raw), spec.transforms)
    if not value:
        return []
    return [tpl.replace("{value}", value) for tpl in spec.templates]


def _field_of(secret: Any, target: str) -> str | None:
    raw = getattr(secret, target, None)
    return None if raw is None else str(raw)


def pick_match(
    spec: RuleSpec,
    wanted: Sequence[str],
    secrets: Sequence[Any],
) -> tuple[Any | None, str]:
    """在给定凭据里找第一条命中的。返回 ``(凭据 or None, 实际用的待匹配值)``。

    ``wanted`` 的顺序就是模板顺序——第一条命中的即生效。
    """
    for candidate in wanted:
        for secret in secrets:
            field = _field_of(secret, spec.target)
            if field is None:
                continue
            if spec.match == "exact_ci":
                if field.lower() == candidate.lower():
                    return secret, candidate
            elif field == candidate:
                return secret, candidate
    return None, wanted[0] if wanted else ""


_TARGET_LABEL = {"secret_key": "secret_key", "name": "名称", "category": "分类"}


def rule_summary(spec: RuleSpec) -> str:
    """给界面用的一句话摘要，例如 ``secret_key = {value}（取入参 to）``。"""
    if spec.is_arg_key:
        source = f"入参 {spec.arg_name}"
    else:
        source = {
            "user.email": "我的邮箱",
            "user.user_name": "我的账号名",
            "user.real_name": "我的姓名",
            "user.dept_code": "我的部门",
            "user.user_id": "我的用户 ID",
        }.get(spec.key_source, spec.key_source)
    target = _TARGET_LABEL.get(spec.target, spec.target)
    return f"{target} = {spec.templates[0]}（取{source}）"


# ── 候选编号 ────────────────────────────────────────────────────────────
#
# 「编号」是模型能看见、也能回传的唯一东西：它不指向任何内部 id，而是**排序后的
# 序号**。只要调用方用同一个排序重建一遍，就能把 ``c3`` 换回同一条凭据。
#
# 为什么不做「编号 → secret_id」的缓存：工具定义是跨用户缓存的，而可见集合是
# 按用户的；一旦编号被固定下来，换个人、换一分钟就可能指到别的凭据。

def choice_field(param_name: str) -> str:
    """模型侧那个「填编号」的参数名。

    参数名可能含远端才允许的字符（``a-b``、``a.b``），Pydantic 字段名必须合法，
    所以统一把非 ``[0-9A-Za-z_]`` 换成下划线。两侧必须用同一个函数，
    否则字段写进去、运行时读不到。

    **不能以下划线开头**：Pydantic 会把 ``_foo`` 当成私有属性并直接报错
    （``Fields must not use names with leading underscores``）。所以用
    ``secret_choice_`` 前缀而不是 ``__secret_choice_``。
    """
    import re

    return "secret_choice_" + re.sub(r"[^0-9A-Za-z_]", "_", str(param_name or ""))


def catalog_sort_key(row: Any) -> tuple[str, str]:
    """候选的固定排序：先按 secret_key，再按 id 兜底。

    ``secret_key`` 在同一个用户名下唯一，但跨用户看别人的系统级凭据时理论上可能
    重名，用 id 兜底保证排序是全序、每次重建都一致。
    """
    return (
        str(getattr(row, "secret_key", "") or ""),
        str(getattr(row, "id", "") or ""),
    )


def parse_choice(text: Any) -> int | None:
    """``c3`` → ``3``。不合法返回 ``None``（当成「模型没选」而不是猜一个）。"""
    raw = str(text or "").strip().lower()
    if not raw.startswith("c"):
        return None
    try:
        index = int(raw[1:])
    except (TypeError, ValueError):
        return None
    return index if index >= 1 else None
