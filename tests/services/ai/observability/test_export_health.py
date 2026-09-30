"""导出健康度：计数、连续失败、错误截断与并发安全。"""

import threading

from app.services.ai.observability import export_health


def setup_function():
    export_health.reset_for_tests()


def test_initial_snapshot_is_empty():
    snap = export_health.snapshot()
    assert snap.success_count == 0
    assert snap.failure_count == 0
    assert snap.consecutive_failures == 0
    assert snap.last_success_at is None
    assert snap.last_failure_at is None
    assert snap.last_error is None


def test_record_success_sets_timestamp_and_counts():
    export_health.record_success()
    snap = export_health.snapshot()
    assert snap.success_count == 1
    assert snap.last_success_at is not None
    assert snap.consecutive_failures == 0


def test_record_failure_accumulates_and_keeps_reason():
    export_health.record_failure(RuntimeError("boom"))
    export_health.record_failure(RuntimeError("again"))
    snap = export_health.snapshot()
    assert snap.failure_count == 2
    assert snap.consecutive_failures == 2
    assert snap.last_failure_at is not None
    assert "again" in snap.last_error


def test_success_resets_consecutive_failures_but_keeps_last_error():
    """成功后连续失败清零，但「最近一次失败原因」保留为历史记录。"""
    export_health.record_failure(RuntimeError("first"))
    export_health.record_success()
    snap = export_health.snapshot()
    assert snap.consecutive_failures == 0
    assert snap.success_count == 1
    assert snap.failure_count == 1
    assert "first" in snap.last_error


def test_error_message_is_truncated():
    export_health.record_failure(RuntimeError("x" * 5000))
    assert len(export_health.snapshot().last_error) <= export_health.MAX_ERROR_LENGTH


def test_record_never_raises_on_exotic_error():
    """record_* 跑在导出线程上，抛异常会干扰导出，因此必须自兜底。"""

    class _BadError(BaseException):
        def __str__(self):
            raise RuntimeError("无法转成字符串")

    export_health.record_failure(_BadError())  # 不得抛出


def test_concurrent_writes_do_not_lose_counts():
    def worker():
        for _ in range(200):
            export_health.record_success()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert export_health.snapshot().success_count == 800
