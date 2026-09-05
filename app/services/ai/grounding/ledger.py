from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.ai.grounding.models import (
    EvidenceReceipt,
    EvidenceStatus,
    EvidenceType,
    FactFreshness,
    ToolResultEnvelope,
)


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
_RESULT_COUNT_KEYS = ("total", "count", "row_count", "affected_rows")


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
    # AgentScope 原生工具可能返回 ToolChunk；空 content 是成功但无证据内容，
    # 不能因为对象本身存在就误判为 SUCCESS_NON_EMPTY。
    if hasattr(result, "content") and hasattr(result, "state"):
        return bool(getattr(result, "content", None))
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
        for key in _RESULT_COUNT_KEYS:
            value = result.get(key)
            if isinstance(value, bool) or value in (None, ""):
                continue
            try:
                if float(value) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        payload_values = [
            value
            for key, value in result.items()
            if key not in {
                "status",
                "success",
                "code",
                "message_type",
                *_RESULT_COUNT_KEYS,
            }
        ]
        return any(_is_non_empty_success_result(value) for value in payload_values)
    return True


def classify_evidence_result(result: Any) -> EvidenceStatus:
    """Classify a tool envelope without treating empty success as failure."""
    if _is_success_result(result):
        return (
            EvidenceStatus.SUCCESS_NON_EMPTY
            if _is_non_empty_success_result(result)
            else EvidenceStatus.SUCCESS_EMPTY
        )
    if result is None:
        return EvidenceStatus.UNAVAILABLE
    if isinstance(result, dict):
        status = str(result.get("status") or result.get("state") or "").strip().lower()
        if status in {"denied", "forbidden"} or "permission" in str(result.get("message") or "").lower():
            return EvidenceStatus.DENIED
        if status in {"unavailable", "not_found", "not_available"}:
            return EvidenceStatus.UNAVAILABLE
    return EvidenceStatus.FAILED


class EvidenceLedger:
    def __init__(self, *, user_id: str | None, conversation_id: str | None) -> None:
        self.user_id = str(user_id) if user_id is not None else None
        self.conversation_id = conversation_id
        self._receipts: list[EvidenceReceipt] = []

    @property
    def receipts(self) -> tuple[EvidenceReceipt, ...]:
        return tuple(self._receipts)

    def rebind_call_id(self, old_call_id: str, new_call_id: str) -> bool:
        """把工具封装层 ID 对齐到 AgentScope 事件中的真实 tool_call_id。

        AgentScope 调用 Python 工具时不会把模型的 ``tool_call_id`` 作为参数
        传入，运行时只能先生成内部 ID，再在结果事件带回元数据后完成对齐。
        这只改收据索引，不改变结果内容或证据资格；若目标 ID 已有收据则丢弃
        旧 ID，避免重试/恢复产生重复凭证。
        """

        old = str(old_call_id or "").strip()
        new = str(new_call_id or "").strip()
        if not old or not new or old == new:
            return False
        if not any(receipt.call_id == old for receipt in self._receipts):
            return False
        if any(receipt.call_id == new for receipt in self._receipts):
            self._receipts = [
                receipt for receipt in self._receipts if receipt.call_id != old
            ]
            return True
        self._receipts = [
            replace(receipt, call_id=new) if receipt.call_id == old else receipt
            for receipt in self._receipts
        ]
        return True

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
                "truncated": receipt.truncated,
                "marker_digests": sorted(receipt.marker_digests),
                "strong_marker_digests": sorted(receipt.strong_marker_digests),
                "empty_success": receipt.empty_success,
                "status": receipt.status.value,
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
                        truncated=bool(raw.get("truncated", False)),
                        marker_digests=frozenset(
                            str(item) for item in raw.get("marker_digests") or []
                        ),
                        strong_marker_digests=frozenset(
                            str(item)
                            for item in raw.get("strong_marker_digests") or []
                        ),
                        empty_success=bool(raw.get("empty_success", False)),
                        status=(
                            EvidenceStatus(str(raw.get("status")))
                            if raw.get("status") is not None
                            else (
                                EvidenceStatus.SUCCESS_EMPTY
                                if bool(raw.get("empty_success", False))
                                else EvidenceStatus.SUCCESS_NON_EMPTY
                            )
                        ),
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
        truncated: bool = False,
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
            status=(
                EvidenceStatus.SUCCESS_NON_EMPTY
                if non_empty_success
                else EvidenceStatus.SUCCESS_EMPTY
            ),
            observed_at=observed_at,
            source_as_of=source_as_of,
            expires_at=expires_at,
            freshness=inferred_freshness,
            source_ref=source_ref,
            truncated=truncated,
        )
        self._receipts.append(receipt)
        return receipt

    def record_envelope(
        self,
        envelope: ToolResultEnvelope,
        *,
        evidence_types: Iterable[EvidenceType],
        policy: str = "non_empty",
        expires_at: datetime | None = None,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
    ) -> EvidenceReceipt | None:
        """仅从已完成且具备证据资格的统一凭证生成收据。

        运行时工具、恢复执行和 ChatBI 最终结果都应走这里。这样展示层的
        thinking/log/summary 文本即使包含数字或业务名词，也没有进入账本的
        入口；失败重试也只会留下最终成功调用对应的收据。
        """

        if not isinstance(envelope, ToolResultEnvelope):
            return None
        if not envelope.evidence_eligible:
            return None
        if envelope.status not in {
            EvidenceStatus.SUCCESS_NON_EMPTY,
            EvidenceStatus.SUCCESS_EMPTY,
        }:
            return None
        return self.record_success(
            call_id=envelope.call_id,
            producer=envelope.producer,
            evidence_types=evidence_types,
            result=envelope.result,
            policy=policy,
            observed_at=envelope.observed_at,
            source_as_of=envelope.source_as_of,
            expires_at=expires_at,
            freshness=freshness,
            source_ref=envelope.source_ref,
            truncated=envelope.truncated,
        )

    def has_valid_evidence(
        self,
        required_types: Iterable[EvidenceType],
        *,
        allow_truncated: bool = True,
    ) -> bool:
        required = frozenset(required_types)
        if not required:
            return any(
                allow_truncated or not receipt.truncated
                for receipt in self._receipts
            )
        return any(
            receipt.evidence_types & required
            and (allow_truncated or not receipt.truncated)
            for receipt in self._receipts
        )

    @staticmethod
    def _normalize_current_time(now: datetime | None) -> datetime:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current

    @staticmethod
    def _receipt_matches_freshness(
        receipt: EvidenceReceipt,
        *,
        requested_freshness: FactFreshness,
        max_age_seconds: int | None,
        require_source_as_of: bool,
        allow_reuse: bool,
        allow_truncated: bool,
        current: datetime,
    ) -> bool:
        if receipt.truncated and not allow_truncated:
            return False
        if receipt.expires_at is not None:
            expires_at = receipt.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= current:
                return False
        if requested_freshness == FactFreshness.REUSE_PREVIOUS and not allow_reuse:
            return False
        if requested_freshness in {
            FactFreshness.DYNAMIC,
            FactFreshness.REALTIME,
        } and receipt.freshness not in {
            FactFreshness.UNKNOWN,
            FactFreshness.DYNAMIC,
            FactFreshness.REALTIME,
        }:
            return False
        if require_source_as_of and receipt.source_as_of is None:
            return False
        if max_age_seconds is not None:
            observed_at = receipt.observed_at or receipt.created_at
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            age_seconds = (current - observed_at).total_seconds()
            if age_seconds > max(0, int(max_age_seconds)):
                return False
        return True

    def has_fresh_evidence(
        self,
        required_types: Iterable[EvidenceType],
        *,
        producer: str | None = None,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        max_age_seconds: int | None = None,
        require_source_as_of: bool = False,
        allow_reuse: bool = False,
        allow_truncated: bool = True,
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
        current = self._normalize_current_time(now)

        for receipt in self._receipts:
            if producer is not None and receipt.producer != str(producer):
                continue
            if required and not (receipt.evidence_types & required):
                continue
            if not self._receipt_matches_freshness(
                receipt,
                requested_freshness=requested_freshness,
                max_age_seconds=max_age_seconds,
                require_source_as_of=require_source_as_of,
                allow_reuse=allow_reuse,
                allow_truncated=allow_truncated,
                current=current,
            ):
                continue
            return True
        return False

    def has_fresh_evidence_from_producer(
        self,
        producer: str,
        required_types: Iterable[EvidenceType],
        *,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        max_age_seconds: int | None = None,
        require_source_as_of: bool = False,
        allow_reuse: bool = False,
        allow_truncated: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """判断指定工具 producer 是否提供了满足时效要求的成功收据。"""
        return self.has_fresh_evidence(
            required_types,
            producer=producer,
            freshness=freshness,
            max_age_seconds=max_age_seconds,
            require_source_as_of=require_source_as_of,
            allow_reuse=allow_reuse,
            allow_truncated=allow_truncated,
            now=now,
        )

    def has_candidate_overlap(
        self,
        candidate_text: str,
        required_types: Iterable[EvidenceType],
        *,
        allow_empty: bool = False,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        max_age_seconds: int | None = None,
        require_source_as_of: bool = False,
        allow_reuse: bool = False,
        allow_truncated: bool = True,
        now: datetime | None = None,
    ) -> bool:
        required = frozenset(required_types)
        try:
            requested_freshness = FactFreshness(freshness)
        except (TypeError, ValueError):
            requested_freshness = FactFreshness.UNKNOWN
        current = self._normalize_current_time(now)
        candidate_markers, candidate_strong_markers = _marker_sets(candidate_text)
        for receipt in self._receipts:
            if required and not (receipt.evidence_types & required):
                continue
            if not self._receipt_matches_freshness(
                receipt,
                requested_freshness=requested_freshness,
                max_age_seconds=max_age_seconds,
                require_source_as_of=require_source_as_of,
                allow_reuse=allow_reuse,
                allow_truncated=allow_truncated,
                current=current,
            ):
                continue
            if allow_empty and receipt.empty_success:
                return True
            if candidate_strong_markers & receipt.strong_marker_digests:
                return True
            if len(candidate_markers & receipt.marker_digests) >= 2:
                return True
        return False

    def has_candidate_overlap_from_producer(
        self,
        candidate_text: str,
        producer: str,
        required_types: Iterable[EvidenceType],
        *,
        allow_empty: bool = False,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        max_age_seconds: int | None = None,
        require_source_as_of: bool = False,
        allow_reuse: bool = False,
        allow_truncated: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """只在指定 producer 的收据中寻找候选回答的关键标记关联。"""
        required = frozenset(required_types)
        try:
            requested_freshness = FactFreshness(freshness)
        except (TypeError, ValueError):
            requested_freshness = FactFreshness.UNKNOWN
        current = self._normalize_current_time(now)
        candidate_markers, candidate_strong_markers = _marker_sets(candidate_text)
        for receipt in self._receipts:
            if receipt.producer != str(producer):
                continue
            if required and not (receipt.evidence_types & required):
                continue
            if not self._receipt_matches_freshness(
                receipt,
                requested_freshness=requested_freshness,
                max_age_seconds=max_age_seconds,
                require_source_as_of=require_source_as_of,
                allow_reuse=allow_reuse,
                allow_truncated=allow_truncated,
                current=current,
            ):
                continue
            if allow_empty and receipt.empty_success:
                return True
            if candidate_strong_markers & receipt.strong_marker_digests:
                return True
            if len(candidate_markers & receipt.marker_digests) >= 2:
                return True
        return False
