"""定时任务模块审查修复的回归单测。

覆盖本次代码审查中定位的 P0/P1/P2 问题：
1. Schema 输入边界（任务名长度、状态范围、历史响应兼容）与 bool 数值防穿透；
2. 报表订阅 PostgreSQL 聚合兼容性契约与管理员操作权限；
3. TaskCenterService 同时修复历史脏 cron 并启用不再死锁、upsert 后刷新 next_run_at、LIKE 转义；
4. 调度器锁跳过不覆盖 running 状态、指标写回防覆盖并发配置、无本地调度器 next_run_at 兜底、
   暂停清理残留 retry Job、后台任务强引用保活；
5. 通知投递长报告不再因“接下来/稍后”被误杀、历史表已落库时轮询首轮即退出；
6. AI 任务工具 docstring 补齐 feishu、入参校验、后台异步触发 run_task_manually 及 owner 类型兼容。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints import tasks as tasks_endpoint
from app.schemas.task import TaskCreate, TaskResponse, TaskUpdate
from app.services import task_center_service as tcs
from app.services import task_notification_delivery as tnd
from app.services.ai import scheduler_service as sched_mod
from app.services.ai.tools import task_manager_tools as tools
from app.services.task_center_service import TaskCenterService, _escape_like
from app.services.task_execution_options import normalize_temperature


pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 1. Schema 与布尔数值边界
# ---------------------------------------------------------------------------


def test_task_schema_validation_and_response_compat():
    with pytest.raises(ValidationError):
        TaskCreate(
            name="a" * 101,
            agent_id="main",
            cron_expr="0 8 * * *",
            prompt="巡检",
        )

    with pytest.raises(ValidationError):
        TaskCreate(
            name="日报",
            agent_id="main",
            cron_expr="0 8 * * *",
            prompt="",
        )

    with pytest.raises(ValidationError):
        TaskUpdate(status=3)

    with pytest.raises(ValidationError):
        TaskUpdate(status=-1)

    with pytest.raises(ValidationError):
        TaskUpdate(name="x" * 101)

    # TaskResponse 不受新建长度约束，兼容历史库中可能存在的超长任务名
    resp = TaskResponse(
        id=1,
        user_id=1,
        name="旧" * 110,
        agent_id="main",
        cron_expr="0 8 * * *",
        prompt="检查",
        status=1,
        conversation_id="conv-legacy",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    assert len(resp.name) == 110


def test_bool_values_rejected_in_numeric_normalizers():
    assert normalize_temperature(True) is None
    assert normalize_temperature(False) is None
    assert normalize_temperature(0.7) == 0.7

    assert sched_mod._clamp_int(True, default=30, minimum=5, maximum=100) == 30
    assert sched_mod._clamp_int(False, default=30, minimum=5, maximum=100) == 30
    assert sched_mod._clamp_int(12, default=30, minimum=5, maximum=100) == 12


# ---------------------------------------------------------------------------
# 2. 报表订阅 PostgreSQL 聚合契约与管理员权限
# ---------------------------------------------------------------------------


def test_list_report_subscriptions_uses_case_aggregation_not_sum_boolean():
    source = inspect.getsource(tasks_endpoint.list_report_subscriptions)
    assert "func.sum(PortalSavedReportRun.status ==" not in source
    assert "case(" in source
    assert "group_by(" in source


def test_extract_user_id_and_owned_report_subscription_admin_access():
    assert tasks_endpoint._extract_user_id({"user_id": 12}) == 12
    assert tasks_endpoint._extract_user_id({"id": "34"}) == 34
    # 认不出用户必须 401，不能退化成 0 —— 0 会被当成「某个用户的 id」继续往下查
    with pytest.raises(tasks_endpoint.HTTPException) as exc_info:
        tasks_endpoint._extract_user_id({})
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_owned_report_subscription_allows_admin_and_blocks_other_user():
    sub = SimpleNamespace(id=9, user_id=100)
    result = MagicMock()
    result.scalar_one_or_none.return_value = sub
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)

    # 管理员可以操作他人订阅
    admin_res = await tasks_endpoint._owned_report_subscription(
        db, 9, {"user_id": 1, "role": "admin"}
    )
    assert admin_res is sub

    # 本人可以操作
    owner_res = await tasks_endpoint._owned_report_subscription(
        db, 9, {"id": 100, "role": "user"}
    )
    assert owner_res is sub

    # 其他普通用户被拒 403（订阅存在但不是自己的：403 不是 404，因为列表里他看得见）
    with pytest.raises(tasks_endpoint.HTTPException) as exc_info:
        await tasks_endpoint._owned_report_subscription(
            db, 9, {"user_id": 200, "role": "user"}
        )
    assert exc_info.value.status_code == 403

    # 订阅不存在才是 404
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=missing)
    with pytest.raises(tasks_endpoint.HTTPException) as exc_info:
        await tasks_endpoint._owned_report_subscription(
            db, 9, {"user_id": 100, "role": "user"}
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 3. TaskCenterService：同时修 cron + 启用、刷新 next_run_at 顺序、LIKE 转义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_allows_fixing_broken_cron_while_enabling_and_refreshes_next_run_after_upsert():
    # 库内历史任务是坏 cron 且暂停中；用户同时把它改成合法 cron 并启用
    broken_task = SimpleNamespace(
        id=42,
        cron_expr="0 0 8 * * *",  # 6 段非法历史表达式
        status=0,
        next_run_at=None,
    )
    updated_task = SimpleNamespace(
        id=42,
        cron_expr="0 8 * * *",
        status=1,
        next_run_at=None,
    )

    first_result = MagicMock()
    first_result.scalar_one_or_none.return_value = broken_task

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[first_result, MagicMock()])
    db.commit = AsyncMock()

    call_order: list[str] = []

    async def fake_upsert(task):
        call_order.append("upsert")

    def fake_get_next_run_time(task_id, **kwargs):
        call_order.append("get_next_run_time")
        return "2026-11-25 08:00:00"

    with (
        patch.object(TaskCenterService, "get_task", AsyncMock(return_value=updated_task)),
        patch.object(tcs, "scheduler_service") as sched,
    ):
        sched.upsert_task = AsyncMock(side_effect=fake_upsert)
        sched.get_next_run_time = MagicMock(side_effect=fake_get_next_run_time)

        result = await TaskCenterService.update_task(
            db,
            42,
            {"cron_expr": "0 8 * * *", "status": 1},
        )

    assert result is updated_task
    assert result.next_run_at == "2026-11-25 08:00:00"
    assert call_order == ["upsert", "get_next_run_time"]


def test_escape_like_escapes_wildcards_and_backslash():
    assert _escape_like(r"a\b%c_d") == r"a\\b\%c\_d"


# ---------------------------------------------------------------------------
# 4. SchedulerService：锁跳过、并发配置不覆盖、next_run_at 兜底、retry 清理与保活
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_task_skipped_preserve_running_keeps_running_status():
    task = SimpleNamespace(
        id=11,
        config={
            "task_metrics": {
                "last_status": "running",
                "health_status": "healthy",
                "skipped_count": 2,
                "last_message": "任务已触发，正在执行",
            }
        },
    )
    written = {}

    async def fake_write(session, target, metrics):
        written.update(metrics)

    with patch.object(sched_mod, "_write_task_metrics", AsyncMock(side_effect=fake_write)):
        res = await sched_mod._mark_task_skipped(
            MagicMock(),
            task,
            reason="已有执行实例在运行",
            preserve_running=True,
        )

    assert res["skipped_count"] == 3
    assert res["last_status"] == "running"
    assert res["health_status"] == "healthy"
    assert res.get("last_skipped_at")
    assert written["last_status"] == "running"


@pytest.mark.asyncio
async def test_write_task_metrics_merges_latest_db_config_instead_of_overwriting():
    # 内存中持有的旧 task.config 没有用户在执行期间 PATCH 新增的 model/notification_channels
    task = SimpleNamespace(
        id=21,
        config={"task_metrics": {"trigger_count": 1}},
    )
    latest_db_config = {
        "model": "gpt-5",
        "notification_channels": ["portal", "feishu"],
        "task_metrics": {"trigger_count": 1},
    }

    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = latest_db_config

    captured_values = {}

    async def fake_execute(stmt):
        if "SELECT" in str(stmt).upper():
            return select_result
        compiled = stmt.compile()
        captured_values.update(compiled.params)
        return MagicMock()

    session = MagicMock()
    session.execute = AsyncMock(side_effect=fake_execute)
    session.commit = AsyncMock()

    await sched_mod._write_task_metrics(session, task, {"trigger_count": 2, "last_status": "success"})

    assert task.config["model"] == "gpt-5"
    assert task.config["notification_channels"] == ["portal", "feishu"]
    assert task.config["task_metrics"]["trigger_count"] == 2
    assert task.config["task_metrics"]["last_status"] == "success"
    session.commit.assert_awaited_once()


def test_get_next_run_time_falls_back_to_cron_calculation_without_local_scheduler(monkeypatch):
    service = sched_mod.TaskSchedulerService()
    monkeypatch.setattr(sched_mod.TaskSchedulerService, "_scheduler", None)

    next_run = service.get_next_run_time(99, cron_expr="0 8 * * *", status=1)
    # 兜底直接算 cron，返回的是 datetime（与 job.next_run_time 同构），不是格式化字符串
    assert isinstance(next_run, datetime)
    assert next_run.strftime("%Y-%m-%d %H:%M:%S")

    assert service.get_next_run_time(99, cron_expr="0 8 * * *", status=0) is None
    assert service.get_next_run_time(99, cron_expr="bad cron", status=1) is None


@pytest.mark.asyncio
async def test_upsert_task_paused_removes_main_and_retry_jobs(monkeypatch):
    service = sched_mod.TaskSchedulerService()
    fake_scheduler = MagicMock()
    fake_scheduler.get_jobs.return_value = [
        SimpleNamespace(id="task_5"),
        SimpleNamespace(id="task_5_retry_1"),
        SimpleNamespace(id="task_6"),
    ]
    monkeypatch.setattr(sched_mod.TaskSchedulerService, "_scheduler", fake_scheduler)
    # 本类是单例：别处调用 stop() 会在实例上留下 `_scheduler = None` 的实例属性，
    # 它会遮蔽上面的类级替换（单跑绿、全量红就是被这条污染的）。
    monkeypatch.delattr(service, "_scheduler", raising=False)

    paused_task = SimpleNamespace(id=5, status=0, cron_expr="0 8 * * *")
    await service.upsert_task(paused_task)

    removed_ids = [call.args[0] for call in fake_scheduler.remove_job.call_args_list]
    assert "task_5" in removed_ids
    assert "task_5_retry_1" in removed_ids
    assert "task_6" not in removed_ids


@pytest.mark.asyncio
async def test_spawn_background_task_retains_reference_and_cleans_up():
    done = asyncio.Event()

    async def sample_job():
        await asyncio.sleep(0.01)
        done.set()

    task = sched_mod.spawn_background_task(sample_job(), label="unit_test_bg")
    assert task in sched_mod._BACKGROUND_TASKS
    await done.wait()
    await task
    # 回调在事件循环下一轮执行
    await asyncio.sleep(0)
    assert task not in sched_mod._BACKGROUND_TASKS


# ---------------------------------------------------------------------------
# 5. 通知投递误杀与轮询提前退出
# ---------------------------------------------------------------------------


def test_provisional_check_does_not_reject_complete_long_report_mentioning_next_steps():
    long_report = (
        "## 本周巡检报告\n\n"
        "本周所有核心数据库节点运行平稳，平均 CPU 负载 28.4%，内存使用率 61.2%，"
        "慢查询数量较上周下降 35%，核心业务接口 P99 响应时间保持在 140ms 以内。\n\n"
        "### 指标汇总\n"
        "- 订单库主节点：QPS 1,820，磁盘剩余 412GB，复制延迟 0.2ms，连接数峰值 340。\n"
        "- 订单库从节点：复制延迟稳定在 0.3ms 以内，全天无切换事件。\n"
        "- 报表分析库：昨日归档任务全部按时完成，无锁等待超时告警，归档耗时 42 分钟。\n"
        "- 缓存集群：命中率 98.7%，未触发淘汰阈值，最大内存占用 71.3%。\n"
        "- 消息队列：积压峰值为 1,204 条，在 90 秒内消费完毕，无死信。\n\n"
        "### 行动建议\n"
        "接下来建议在下周维护窗口对历史日志表做一次分区归档，稍后如有新的扩容需求可再统一评估。"
    )
    assert len(long_report) > 320
    assert tnd.is_provisional_assistant_text(long_report) is False

    # 正文中段出现强信号、但结尾是结论 —— 不该误杀（这是按全文匹配时的典型误杀）
    mid_signal = (
        "本周已完成对最近 30 天全部告警数据的核对与归类。正在分析的 12 条告警均已闭环，"
        "其中 7 条为容量水位临时越界，5 条为上游接口超时重试导致，均无需人工介入。"
        "数据库层面无慢查询堆积，缓存命中率保持 98% 以上，消息队列无积压。"
        "整体可用性 99.98%，符合 SLA 要求，本轮无需额外处理或跟进。"
    )
    assert len(mid_signal) > 80
    assert tnd.is_provisional_assistant_text(mid_signal) is False

    # 短半截话依然拦截
    assert tnd.is_provisional_assistant_text("我先查一下数据，稍后回复您。") is True
    # 长文本末尾若出现强半截话信号依然拦截
    trailing_provisional = ("这是一段较长的前置说明内容。" * 25) + "请稍候，我正在查询最新报表数据..."
    assert len(trailing_provisional) > 320
    assert tnd.is_provisional_assistant_text(trailing_provisional) is True

    # 超过 160 字、但结尾仍在承诺后续 —— 原先按长度分桶会漏判，现在必须拦住
    long_then_promise = (
        "先核对了 A、B、C 三项配置与近三日的调用日志，均无异常。" * 8 + "稍后回复您。"
    )
    assert len(long_then_promise) > 160
    assert tnd.is_provisional_assistant_text(long_then_promise) is True

    # 正常报告里的「接下来」「稍后」不再作为任何依据：
    # 词表只留明确的承诺短语（稍后回复/稍后补充/稍后再/请稍等），
    # 分不出「接下来建议做 X」（结论）和「接下来我再查 X」（承诺），索性只拦后者。
    assert tnd.is_provisional_assistant_text("接下来建议在下周维护窗口做一次分区归档。") is False
    assert tnd.is_provisional_assistant_text("稍后季度调整时可再统一评估容量规划。") is False


@pytest.mark.asyncio
async def test_load_delivered_notification_tools_exits_immediately_when_history_ready():
    trace_result = MagicMock()
    trace_result.scalars.return_value.all.return_value = []
    history_result = MagicMock()
    history_result.scalar_one_or_none.return_value = 888

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[trace_result, history_result])

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    sleep_mock = AsyncMock()
    with (
        patch.object(tnd, "AsyncSessionLocal", return_value=cm),
        patch.object(tnd.asyncio, "sleep", sleep_mock),
    ):
        delivered = await tnd.load_delivered_notification_tools("trace-ready-1")

    assert delivered == set()
    # 首轮检测到历史记录已落库即退出，不进入任何 sleep 等待
    sleep_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. AI 任务管理工具：feishu 说明、参数校验、异步手动触发与权限类型兼容
# ---------------------------------------------------------------------------


def test_create_recurring_task_docstring_mentions_feishu_and_keeps_cron_contract():
    # create_recurring_task 被 FunctionTool 包过，包装对象自身没有 docstring，
    # 工具描述取自 inspect.getdoc(func)，所以这里要读 .func
    doc = inspect.getdoc(tools.create_recurring_task.func) or ""
    assert "feishu" in doc
    assert "exactly 5 fields" in doc
    assert "0 or 7 = Sunday" in doc
    assert "Do NOT use 6 fields" in doc
    assert "Do NOT restrict both day-of-month and day-of-week" in doc


@pytest.mark.asyncio
async def test_create_recurring_task_validates_name_length_and_agent_id():
    ctx_no_agent = SimpleNamespace(user_id=7, agent_id="", is_admin=False)
    with patch.object(tools, "get_current_agent_context", return_value=ctx_no_agent):
        msg = await tools.create_recurring_task.func("巡检", "0 8 * * *", "检查负载")
    assert "agent_id" in msg

    ctx_ok = SimpleNamespace(user_id=7, agent_id="main", is_admin=False)
    with patch.object(tools, "get_current_agent_context", return_value=ctx_ok):
        msg = await tools.create_recurring_task.func("长" * 101, "0 8 * * *", "检查负载")
    assert "too long" in msg


@pytest.mark.asyncio
async def test_run_task_manually_spawns_background_task_and_handles_str_int_user_id():
    ctx = SimpleNamespace(user_id="7", agent_id="main", is_admin=False)
    task_obj = SimpleNamespace(id=15, user_id=7, name="日报")

    fake_session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_session)
    cm.__aexit__ = AsyncMock(return_value=False)

    spawn_calls = []

    def fake_spawn(coro, *, label=""):
        spawn_calls.append(label)
        coro.close()
        return MagicMock()

    with (
        patch.object(tools, "get_current_agent_context", return_value=ctx),
        patch.object(tools, "AsyncSessionLocal", return_value=cm),
        patch.object(TaskCenterService, "get_task", AsyncMock(return_value=task_obj)),
        # 打在模块上：工具函数内部是 from ... import spawn_background_task，
        # 而 scheduler_service 实例从来没有这个属性（这正是本轮修的回归）
        patch.object(sched_mod, "spawn_background_task", side_effect=fake_spawn),
    ):
        msg = await tools.run_task_manually.func(15)

    assert "已在后台触发" in msg
    # 后台化后拿不到本次结果，必须引导用户去看执行历史，否则「已触发」会被当成「已成功」
    assert "执行历史" in msg
    assert spawn_calls == ["tool_manual_task_15"]


# ---------------------------------------------------------------------------
# 7. 本轮补充修复：短结论不被误判、外部渠道异常不污染会话、周期清理 running
# ---------------------------------------------------------------------------


def test_short_complete_result_is_delivered():
    """短不等于没结论。

    定时任务经常只返回「已完成，无异常。」这类一句话结论。按长度一律判 too_short
    的结果是每次投递都记失败并触发告警——用户收到的是「投递失败」而不是任务结果。
    """
    ok, reason = tnd.assess_delivery_completeness(
        "已完成，无异常。", has_sql_data=False, had_sql_tool=False
    )
    assert (ok, reason) == (True, "ok")

    # 短且停在续写位置（逗号/省略号）仍然拒发：那是真的没写完
    ok, reason = tnd.assess_delivery_completeness(
        "我先查一下，", has_sql_data=False, had_sql_tool=False
    )
    assert ok is False
    assert reason == "too_short"


@pytest.mark.asyncio
async def test_external_channel_failure_returns_failure_instead_of_raising():
    """外部渠道抛异常必须被吞成投递失败。

    否则异常会穿到调度器，而共享会话已被标脏，紧接着写投递指标的 SELECT 直接
    PendingRollbackError，指标和告警一起丢。
    """

    async def boom(*args, **kwargs):
        raise RuntimeError("webhook 503")

    with patch.object(tnd, "send_external_channel", boom):
        ok, detail = await tnd._deliver_channel(
            MagicMock(),
            user_id=1,
            channel="dingtalk",
            title="标题",
            body="正文",
            trace_id="trace-1",
            task_name="日报",
        )

    assert ok is False
    assert "webhook 503" in detail


@pytest.mark.asyncio
async def test_reconcile_job_also_cleans_stale_running(monkeypatch):
    """周期对账必须顺带清理残留 running。

    只在 start() 里清一次时，重启那次几乎必然被「死进程的锁还没到期」挡掉，
    之后再没有第二次机会，任务状态永远停在「执行中」。
    """
    called: list[str] = []

    async def fake_reload_tasks():
        called.append("reload_tasks")

    async def fake_reload_subs():
        called.append("reload_subs")

    async def fake_cleanup():
        called.append("cleanup_running")

    monkeypatch.setattr(sched_mod.scheduler_service, "reload_tasks", fake_reload_tasks)
    monkeypatch.setattr(
        sched_mod.scheduler_service, "reload_saved_report_subscriptions", fake_reload_subs
    )
    monkeypatch.setattr(
        sched_mod.scheduler_service, "_cleanup_stale_running_tasks", fake_cleanup
    )

    await sched_mod._system_scheduler_reconcile_job()

    assert "cleanup_running" in called


# ---------------------------------------------------------------------------
# 8. 系统维护任务的 Redis 锁：拿不到锁不等于不能干活
# ---------------------------------------------------------------------------


class _BoomRedisClient:
    """模拟 Redis 键都在、但 SET 直接连不上。"""

    async def set(self, *args, **kwargs):
        raise ConnectionError("redis unreachable")


def _make_body_hit(monkeypatch, reached: list[str], label: str):
    """把任务主体第一处依赖换成「一碰就记一笔」，用来判断它到底跑没跑。"""

    class BodyHit(RuntimeError):
        pass

    def fake_session():
        reached.append(label)
        raise BodyHit("body reached")

    monkeypatch.setattr(sched_mod, "AsyncSessionLocal", fake_session)


@pytest.mark.asyncio
async def test_memory_consolidation_runs_when_redis_lock_unavailable(monkeypatch):
    """Redis 拿不到锁时记忆整合照跑。

    这是日频维护任务，锁只为省掉多节点重复劳动。Redis 一挂就整天不跑，记忆不再
    整理、而且没人会发现；重复跑一遍最坏是浪费 CPU。
    """
    reached: list[str] = []
    monkeypatch.setattr(sched_mod.redis, "redis_client", _BoomRedisClient())
    _make_body_hit(monkeypatch, reached, "memory")

    await sched_mod._system_memory_consolidation_job()

    assert reached == ["memory"]


@pytest.mark.asyncio
async def test_third_party_user_sync_runs_when_redis_lock_unavailable(monkeypatch):
    """同步是 upsert，重复执行安全，宁可多跑也不要整天不同步。"""
    from app.services.user_sync_service import UserSyncService

    reached: list[str] = []

    async def boom_body_hit():
        reached.append("sync")
        raise RuntimeError("body reached")

    monkeypatch.setattr(sched_mod.redis, "redis_client", _BoomRedisClient())
    # 主体第一处依赖是 get_config（staticmethod），拿它当「进没进主体」的探针
    monkeypatch.setattr(UserSyncService, "get_config", boom_body_hit)

    await sched_mod._system_third_party_user_sync_job()

    assert reached == ["sync"]


@pytest.mark.asyncio
async def test_knowledge_metrics_sync_tolerates_none_redis_client(monkeypatch):
    """redis_client 为 None 时不能靠 AttributeError 撞进 except 才放行。"""
    from app.services.knowledge_metrics_service import KnowledgeMetricsService

    reached: list[str] = []

    async def boom_body_hit(cls):
        reached.append("metrics")
        raise RuntimeError("body reached")

    monkeypatch.setattr(sched_mod.redis, "redis_client", None)
    # 主体只有一句 sync_redis_metrics_to_db()，拿它当探针
    monkeypatch.setattr(
        KnowledgeMetricsService, "sync_redis_metrics_to_db", classmethod(boom_body_hit)
    )

    await sched_mod._system_knowledge_metrics_sync_job()

    assert reached == ["metrics"]
