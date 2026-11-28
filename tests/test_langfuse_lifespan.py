"""启动/关闭接线契约测试。

追踪是旁路能力：接入点必须
1. 在 lifespan 启动段初始化（不阻塞启动，失败只降级）；
2. 在关闭段 flush 并关闭 client，避免进程退出丢最后一批 span；
3. 用函数级 import，避免启动期强依赖 langfuse 安装。
"""

import inspect

from app import main


def test_lifespan_initializes_observability():
    source = inspect.getsource(main.lifespan)
    assert "init_observability" in source


def test_lifespan_shuts_down_observability():
    source = inspect.getsource(main.lifespan)
    assert "shutdown_observability" in source


def test_observability_init_is_imported_lazily():
    """启动段必须是函数级 import：langfuse 未安装时也要能起服务。"""
    source = inspect.getsource(main.lifespan)
    assert "from app.services.ai.observability.manager import" in source
    # 模块顶层不允许直接 import langfuse
    module_source = inspect.getsource(main)
    assert "import langfuse" not in module_source


def test_observability_init_is_wrapped_for_degradation():
    """初始化必须被降级兜底包住，任何异常/超时都不能阻断启动。

    兜底形式从「裸 try/except」升级为 ``run_startup_step``：裸 try/except 只兜得住
    抛出的异常，兜不住「卡住」——冷启动时若读配置的库不响应，协程会永远停在 await
    上，服务既不报错也起不来。``run_startup_step`` 同时提供 deadline 与异常降级。
    """
    source = inspect.getsource(main.lifespan)
    step_index = source.index("async def _init_observability_step")
    tail = source[step_index : step_index + 700]
    assert "init_observability" in tail
    # 必须真的交给 run_startup_step（自带 wait_for + 异常降级），不能裸 await
    assert "run_startup_step(" in tail
    assert "await run_startup_step(" in tail


def test_shutdown_runs_before_database_close():
    """关闭顺序：先 flush 追踪，再关数据库/Redis 连接。"""
    source = inspect.getsource(main.lifespan)
    assert source.index("shutdown_observability") < source.rindex("close_db")
