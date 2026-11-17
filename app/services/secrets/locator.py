"""凭据定位规则：把「这次调用的入参」翻译成「要找的 secret_key」。

**纯函数，不查库、不解密。** 运行期解析（``resolver._locate``）与配置页试算
（``POST /secrets/locate-preview``）走的是同一段逻辑，否则「试算说能命中、
真跑却失败」这类问题永远查不清。

规则描述的是**怎么找**，不是**找到谁**：``parse_rule`` 会拒绝任何 ``secret_id``，
所以改一条规则永远变不成「读别人凭据」的手段。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

#: 身份类键来源（补充能力，主场景是 ``arg:<入参名>``）
USER_KEY_SOURCES = (
    "user.email",
    "user.user_name",
    "user.real_name",
    "user.dept_code",
    "user.user_id",
)
TRANSFORMS = ("trim", "strip_domain", "lower")
TARGETS = ("secret_key", "name", "category", "field")
MATCHES = ("exact", "exact_ci")
ON_MISS = ("fail", "fallback_fixed", "model")
MAX_TEMPLATES = 3
ALLOWED_FIELDS = {
    "key_source",
    "transforms",
    "templates",
    "target",
    "match",
    "on_miss",
    # 结构约定：命中的凭据必须都是这个模板。有了它，字段集合在配置期就确定，
    # 「取用字段」能给成下拉，运行时也能校验命中的那条结构对不对。
    "value_template",
    # 只有 target=field 时用：匹配凭据里哪个字段的**值**
    "target_field",
}


#: 模板 id 由前端模板常量定义，形如 ``smtp`` / ``mysql`` / ``custom``
# 与 center._TEMPLATE_PATTERN 同一口径（那边是存库校验，这边是规则校验）。
# 两处不一致时，一条规则能通过规则校验却被凭据侧判非法，排查起来极难对号。
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


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
    #: 只接受这个模板的凭据；空串 = 不限。模板不符按**未命中**处理。
    value_template: str = ""
    #: ``target="field"`` 时匹配凭据里哪个字段的值（如 ``username``）。
    #:
    #: 「调用方给了邮箱，找 username 字段等于这个邮箱的凭据」是最常见的诉求，而
    #: secret_key / name / category 都是元数据：key 得是一串标识、name 是给人看的
    #: 中文描述，都接不住业务值。按字段值匹配需要先解密，所以只在 target=field 时
    #: 才走这条重路径。
    target_field: str = ""

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
    # 空 key_source 是合法形态，但**只在**「让模型挑」下成立：那套配法没有
    # 「拿什么去比」这一步，候选由模型从可见集合里挑（on_miss=model）。
    # 其余情况仍必须给出值来源，否则规则算不出候选、必然未命中，
    # 让这种规则存进库只会变成运行时才发现的坏配置。
    if not key_source:
        if str(data.get("on_miss") or "").strip() != "model":
            raise RuleError(
                "缺少 key_source（用哪个值去匹配）；"
                "想交给模型挑请把 on_miss 设成 model"
            )
    elif key_source.startswith("arg:"):
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
    # 按字段值匹配要先解密，比读元数据重得多，所以只在这个目标下开放
    target_field = ""
    if target == "field":
        target_field = str(data.get("target_field") or "").strip()
        if not target_field:
            raise RuleError(
                "target=field 时必须给出 target_field（凭据里哪个字段参与匹配，"
                "例如 username）"
            )
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$", target_field):
            raise RuleError(f"target_field 格式不对：{target_field!r}")
    elif str(data.get("target_field") or "").strip():
        # 写了字段名却选了别的目标：静默忽略会让人以为「按字段匹配」生效了，
        # 实际还在按 name / secret_key 匹配，而且照样能配上——最难查的那种。
        raise RuleError(
            "给了 target_field 但 target 不是 field；按字段值匹配要写 target='field'"
        )
    match = data.get("match") or "exact_ci"
    if match not in MATCHES:
        raise RuleError(f"match 不支持 {match!r}；可用 {' / '.join(MATCHES)}")

    on_miss = data.get("on_miss") or "fail"
    if on_miss not in ON_MISS:
        raise RuleError(f"on_miss 不支持 {on_miss!r}；可用 {' / '.join(ON_MISS)}")

    value_template = str(data.get("value_template") or "").strip()
    if value_template and not _TEMPLATE_ID_RE.match(value_template):
        raise RuleError(
            f"value_template 格式不对：{value_template!r}（应形如 smtp / mysql，"
            "或留空表示不限）"
        )

    return RuleSpec(
        key_source=key_source,
        transforms=tuple(transforms),
        templates=tuple(templates),
        target=target,
        match=match,
        on_miss=on_miss,
        value_template=value_template,
        target_field=target_field,
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


def _stringify(raw: Any) -> str:
    from app.services.secrets.references import stringify_field_value

    return stringify_field_value(raw)


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
    # 和凭据字段侧用同一个转换函数（resolver.field_value_resolver 走 stringify_field_value）：
    # 入参是 JSON 原生 true / 465.0 时，裸 str() 会算出 "True" / "465.0"，
    # 而凭据字段是 "true" / "465"——两边各自算，结果就是「试算命中、真跑未命中」。
    value = _apply_transforms(_stringify(raw), spec.transforms)
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
    *,
    field_value: Callable[[Any], str | None] | None = None,
) -> tuple[Any | None, str]:
    """在给定凭据里找第一条命中的。返回 ``(凭据 or None, 实际用的待匹配值)``。

    ``wanted`` 的顺序就是模板顺序——第一条命中的即生效。

    ``field_value`` 只在 ``target="field"`` 时参与：key / name / category 都是凭据表
    上的列，直接读；而「字段值」要先解密再解析 JSON，这一步只有调用方做得了
    （它才知道怎么解密、以及「现在」是几点）。没给回调就当作取不到——宁可未命中，
    也不要拿元数据凑一个假的命中。
    """
    for candidate in wanted:
        for secret in secrets:
            if spec.target == "field":
                field = field_value(secret) if field_value is not None else None
            else:
                field = _field_of(secret, spec.target)
            if field is None:
                continue
            if spec.match == "exact_ci":
                if field.lower() == candidate.lower():
                    return secret, candidate
            elif field == candidate:
                return secret, candidate
    return None, wanted[0] if wanted else ""


_TARGET_LABEL = {
    "secret_key": "secret_key",
    "name": "名称",
    "category": "分类",
    "field": "字段",
}


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
    if spec.target == "field" and spec.target_field:
        target = f"字段 {spec.target_field}"
    if not spec.key_source:
        # 「让模型挑」没有取值来源，摘要要说明这件事，否则会渲染成「（取）」
        return "由模型在可见凭据里挑一条"
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
