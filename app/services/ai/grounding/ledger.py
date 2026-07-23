from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.ai.grounding.models import EvidenceReceipt, EvidenceType, FactFreshness


_ASCII_MARKER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,}")
_CHINESE_SEQUENCE_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_STRONG_IDENTIFIER_RE = re.compile(
    r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9_.:-]+$"
)
_STRONG_DATE_TIME_RE = re.compile(
    r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}:\d{2}(?::\d{2})?)$"
)
_MARKER_STOPWORDS = {
    "success",
    "true",
    "false",
    "content",
    "data",
    "items",
    "result",
    "status",
    "message",
    "number",
    "price",
}


def _digest_marker(marker: str) -> str:
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()


def _marker_sets(value: Any) -> tuple[frozenset[str], frozenset[str]]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    markers: set[str] = set()
    strong_markers: set[str] = set()
    for match in _ASCII_MARKER_RE.findall(text):
        normalized = match.strip("._:-").lower()
        if len(normalized) >= 2 and normalized not in _MARKER_STOPWORDS:
            markers.add(normalized)
            if (
                _STRONG_IDENTIFIER_RE.match(normalized)
                or _STRONG_DATE_TIME_RE.match(normalized)
            ):
                strong_markers.add(normalized)
    for sequence in _CHINESE_SEQUENCE_RE.findall(text):
        if len(sequence) == 2:
            markers.add(sequence)
        else:
            markers.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
    return (
        frozenset(_digest_marker(marker) for marker in markers),
        frozenset(_digest_marker(marker) for marker in strong_markers),
    )


def _marker_digests(value: Any) -> frozenset[str]:
    return _marker_sets(value)[0]


def _parse_datetime(value: Any, *, fallback: datetime | None = None) -> datetime | None:
    if value in (None, ""):
        return fallback
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def _infer_freshness(evidence_types: frozenset[EvidenceType]) -> FactFreshness:
    if EvidenceType.RUNTIME_STATE in evidence_types:
        return FactFreshness.REALTIME
    if evidence_types and evidence_types <= {EvidenceType.CONVERSATION_MEMORY}:
        return FactFreshness.REUSE_PREVIOUS
    if evidence_types & {
        EvidenceType.INTERNAL_DATA,
        EvidenceType.INTERNAL_KNOWLEDGE,
        EvidenceType.PUBLIC_WEB,
        EvidenceType.USER_FILE,
        EvidenceType.EXTERNAL_TOOL,
    }:
        return FactFreshness.DYNAMIC
    return FactFreshness.UNKNOWN


def _parse_freshness(value: Any) -> FactFreshness:
    try:
        return FactFreshness(str(value or FactFreshness.UNKNOWN.value))
    except (TypeError, ValueError):
        return FactFreshness.UNKNOWN


def _is_error_like_text(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith(
        (
            "错误",
            "失败",
            "[tool_error]",
            "[mcp error]",
            "[execution error]",
            "[error]",
            "error:",
            "permission denied",
        )
    )


def _is_error_control_message(text: str) -> bool:
    lowered = text.strip().lower()
    if _is_error_like_text(lowered):
        return True
    return bool(
        re.search(
            r"(?:(?:执行|调用|查询|读取|检索|搜索|连接|认证|授权).{0,6}"
            r"(?:失败|异常|错误|拒绝)|(?:无权限|权限不足))\s*[。.!！]?$",
            lowered,
        )
    )


def _is_success_result(result: Any) -> bool:
    """Whether the tool call completed successfully, independently of payload size."""
    if result is None:
        return False
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        return False
    result_state = str(getattr(result, "state", "") or "").strip().lower()
    if any(marker in result_state for marker in ("error", "failed", "failure", "denied")):
        return False
    if isinstance(result, str):
        text = result.strip()
        if not text or _is_error_like_text(text):
            return False
        if text.startswith(("{", "[")):
            try:
                return _is_success_result(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return True
    if isinstance(result, dict):
        explicit_success = result.get("success") is True
        if bool(result.get("isError") or result.get("is_error")):
            return False
        if result.get("success") is False:
            return False
        try:
            if int(result.get("code")) >= 400:
                return False
        except (TypeError, ValueError):
            pass
        status = str(result.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure", "denied"}:
            return False
        state = str(result.get("state") or "").strip().lower()
        if state in {"error", "failed", "failure", "denied", "interrupted"}:
            return False
        error_value = result.get("error")
        if error_value not in (None, "", False, [], {}):
            return False
        message = result.get("message")
        if (
            not explicit_success
            and isinstance(message, str)
            and _is_error_control_message(message)
        ):
            return False
        return True
    return True


def _is_non_empty_success_result(result: Any) -> bool:
    if not _is_success_result(result):
        return False
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return False
        lowered = text.lower()
        if any(
            marker in lowered
            for marker in (
                "结果为空",
                "无查询结果",
                "未找到匹配",
                "未产生可交付",
                "no results",
                "not found",
            )
        ):
            return False
        if text.startswith(("{", "[")):
            try:
                return _is_non_empty_success_result(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return True
    if isinstance(result, (list, tuple, set, frozenset)):
        return bool(result)
    if isinstance(result, dict):
        if not result:
            return False
        payload_values = [
            value
            for key, value in result.items()
            if key not in {"status", "success", "code", "message_type"}
        ]
        return any(_is_non_empty_success_result(value) for value in payload_values)
    return True


class EvidenceLedger:
    def __init__(self, *, user_id: str | None, conversation_id: str | None) -> None:
        self.user_id = str(user_id) if user_id is not None else None
        self.conversation_id = conversation_id
        self._receipts: list[EvidenceReceipt] = []

    @property
    def receipts(self) -> tuple[EvidenceReceipt, ...]:
        return tuple(self._receipts)

    @property
    def available_evidence_types(self) -> frozenset[EvidenceType]:
        return frozenset(
            evidence_type
            for receipt in self._receipts
            for evidence_type in receipt.evidence_types
        )

    def to_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "call_id": receipt.call_id,
                "producer": receipt.producer,
                "evidence_types": sorted(item.value for item in receipt.evidence_types),
                "payload_digest": receipt.payload_digest,
                "user_id": receipt.user_id,
                "conversation_id": receipt.conversation_id,
                "created_at": receipt.created_at.isoformat(),
                "observed_at": (
                    receipt.observed_at.isoformat() if receipt.observed_at else None
                ),
                "source_as_of": (
                    receipt.source_as_of.isoformat() if receipt.source_as_of else None
                ),
                "expires_at": (
                    receipt.expires_at.isoformat() if receipt.expires_at else None
                ),
                "freshness": receipt.freshness.value,
                "source_ref": receipt.source_ref,
                "marker_digests": sorted(receipt.marker_digests),
                "strong_marker_digests": sorted(receipt.strong_marker_digests),
                "empty_success": receipt.empty_success,
            }
            for receipt in self._receipts
        ]

    @classmethod
    def from_snapshot(
        cls,
        receipts: Any,
        *,
        user_id: str | None,
        conversation_id: str | None,
    ) -> "EvidenceLedger":
        ledger = cls(user_id=user_id, conversation_id=conversation_id)
        if not isinstance(receipts, list):
            return ledger
        for raw in receipts:
            if not isinstance(raw, dict):
                continue
            receipt_user_id = str(raw.get("user_id")) if raw.get("user_id") is not None else None
            receipt_conversation_id = raw.get("conversation_id")
            if (
                receipt_user_id != ledger.user_id
                or receipt_conversation_id != ledger.conversation_id
            ):
                continue
            try:
                evidence_types = frozenset(
                    EvidenceType(item)
                    for item in raw.get("evidence_types") or []
                )
                if not evidence_types:
                    continue
                ledger._receipts.append(
                    EvidenceReceipt(
                        call_id=str(raw["call_id"]),
                        producer=str(raw["producer"]),
                        evidence_types=evidence_types,
                        payload_digest=str(raw["payload_digest"]),
                        user_id=receipt_user_id,
                        conversation_id=receipt_conversation_id,
                        created_at=(created_at := datetime.fromisoformat(str(raw["created_at"]))),
                        observed_at=_parse_datetime(
                            raw.get("observed_at"), fallback=created_at
                        ),
                        source_as_of=_parse_datetime(raw.get("source_as_of")),
                        expires_at=_parse_datetime(raw.get("expires_at")),
                        freshness=_parse_freshness(raw.get("freshness")),
                        source_ref=(
                            str(raw.get("source_ref"))
                            if raw.get("source_ref") is not None
                            else None
                        ),
                        marker_digests=frozenset(
                            str(item) for item in raw.get("marker_digests") or []
                        ),
                        strong_marker_digests=frozenset(
                            str(item)
                            for item in raw.get("strong_marker_digests") or []
                        ),
                        empty_success=bool(raw.get("empty_success", False)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return ledger

    def record_success(
        self,
        *,
        call_id: str,
        producer: str,
        evidence_types: Iterable[EvidenceType],
        result: Any,
        policy: str = "non_empty",
        observed_at: datetime | None = None,
        source_as_of: datetime | None = None,
        expires_at: datetime | None = None,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        source_ref: str | None = None,
    ) -> EvidenceReceipt | None:
        """记录工具调用取证收据。

        Args:
            policy: ``"non_empty"``（默认）—— 仅在成功且结果非空时记录；
                    ``"allow_empty_success"`` —— 成功调用即使结果为空也记录；
                    错误、拒绝和失败结果始终不记录。
        """
        normalized_types = frozenset(evidence_types)
        if not normalized_types:
            return None
        if not _is_success_result(result):
            return None
        if policy != "allow_empty_success" and not _is_non_empty_success_result(result):
            return None
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        non_empty_success = _is_non_empty_success_result(result)
        marker_digests, strong_marker_digests = (
            _marker_sets(result)
            if non_empty_success
            else (frozenset(), frozenset())
        )
        inferred_freshness = (
            freshness
            if freshness is not FactFreshness.UNKNOWN
            else _infer_freshness(normalized_types)
        )
        receipt = EvidenceReceipt.create(
            call_id=call_id,
            producer=producer,
            evidence_types=normalized_types,
            payload_digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            marker_digests=marker_digests,
            strong_marker_digests=strong_marker_digests,
            empty_success=not non_empty_success,
            observed_at=observed_at,
            source_as_of=source_as_of,
            expires_at=expires_at,
            freshness=inferred_freshness,
            source_ref=source_ref,
        )
        self._receipts.append(receipt)
        return receipt

    def has_valid_evidence(self, required_types: Iterable[EvidenceType]) -> bool:
        required = frozenset(required_types)
        if not required:
            return bool(self._receipts)
        return any(receipt.evidence_types & required for receipt in self._receipts)

    def has_fresh_evidence(
        self,
        required_types: Iterable[EvidenceType],
        *,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        max_age_seconds: int | None = None,
        require_source_as_of: bool = False,
        allow_reuse: bool = False,
        now: datetime | None = None,
    ) -> bool:
        """判断指定来源是否存在满足时效要求的证据。

        旧收据没有 freshness 字段时按 ``UNKNOWN`` 处理，仍可通过原有门禁；
        只有调用方明确声明了年龄窗口、过期时间或来源时间要求时，才会新增
        时效性约束。
        """
        required = frozenset(required_types)
        try:
            requested_freshness = FactFreshness(freshness)
        except (TypeError, ValueError):
            requested_freshness = FactFreshness.UNKNOWN
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        for receipt in self._receipts:
            if required and not (receipt.evidence_types & required):
                continue
            if receipt.expires_at is not None:
                expires_at = receipt.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= current:
                    continue
            if requested_freshness == FactFreshness.REUSE_PREVIOUS and not allow_reuse:
                continue
            if requested_freshness in {
                FactFreshness.DYNAMIC,
                FactFreshness.REALTIME,
            } and receipt.freshness not in {
                FactFreshness.UNKNOWN,
                FactFreshness.DYNAMIC,
                FactFreshness.REALTIME,
            }:
                continue
            if require_source_as_of and receipt.source_as_of is None:
                continue
            if max_age_seconds is not None:
                observed_at = receipt.observed_at or receipt.created_at
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=timezone.utc)
                age_seconds = (current - observed_at).total_seconds()
                if age_seconds > max(0, int(max_age_seconds)):
                    continue
            return True
        return False

    def has_candidate_overlap(
        self,
        candidate_text: str,
        required_types: Iterable[EvidenceType],
        *,
        allow_empty: bool = False,
    ) -> bool:
        required = frozenset(required_types)
        candidate_markers, candidate_strong_markers = _marker_sets(candidate_text)
        for receipt in self._receipts:
            if required and not (receipt.evidence_types & required):
                continue
            if allow_empty and receipt.empty_success:
                return True
            if candidate_strong_markers & receipt.strong_marker_digests:
                return True
            if len(candidate_markers & receipt.marker_digests) >= 2:
                return True
        return False
