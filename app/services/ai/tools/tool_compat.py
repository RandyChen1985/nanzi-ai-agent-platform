from __future__ import annotations

import asyncio
import inspect
import typing
from typing import Any, Callable, Type

from pydantic import BaseModel, Field, create_model


def _schema_from_signature(func: Callable[..., Any]) -> Type[BaseModel]:
    """从函数签名生成参数模型。

    ``from __future__ import annotations`` 会让 ``inspect.signature`` 拿到**字符串**
    注解（``"Optional[str]"``）。直接交给 ``create_model`` 时，pydantic 只在
    tool_compat 这个模块的命名空间里找 ``Optional``，找不到就留下一个「未定义」的
    前向引用，等到 ``model_json_schema()`` 才抛 ``PydanticUserError`` —— 表现为该工具
    在给模型生成工具清单时整体失效，而错误信息完全指不到源头。

    所以先用 ``get_type_hints`` 在**函数自己的模块**里解析注解；解析不出来的（真正的
    前向引用）退回 ``str``，宁可丢类型也不能让 schema 变成不可生成的坏模型。
    """
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 - 注解解析失败不能影响工具注册
        hints = {}

    fields: dict[str, tuple[type[Any], Any]] = {}
    signature = inspect.signature(func)
    for name, param in signature.parameters.items():
        annotation = hints.get(name, param.annotation)
        if annotation is inspect._empty or isinstance(annotation, str):
            annotation = str
        default = param.default if param.default is not inspect._empty else ...
        fields[name] = (annotation, Field(default=default))
    return create_model(f"{func.__name__}Args", **fields)


class BaseTool:
    name: str = ""
    description: str = ""
    args_schema: Type[BaseModel] | None = None

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        if hasattr(self, "_arun"):
            result = self._arun(**arguments)
        elif hasattr(self, "_run"):
            result = self._run(**arguments)
        elif callable(self):
            result = self(**arguments)
        else:
            raise TypeError(f"Tool {self.name or type(self).__name__} is not callable")
        if inspect.isawaitable(result):
            return await result
        return result

    async def arun(self, **kwargs: Any) -> Any:
        return await self.ainvoke(kwargs)

    def invoke(self, arguments: dict[str, Any] | None = None) -> Any:
        result = self.ainvoke(arguments or {})
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("Cannot use sync invoke while an event loop is running; use ainvoke instead")
        return asyncio.run(result)


async def resolve_tool_invocation_result(value: Any) -> Any:
    """把工具调用结果归一成业务值。

    MCP 工具走 agent 链路时返回**异步生成器**（中间是进度片，最后一片是结果）；
    管理台「测试工具」这类直接调用方只需要最终业务值——把生成器对象直接回给前端
    会得到 `<async_generator object ...>`，而且远端调用根本没被执行。
    """
    if inspect.isasyncgen(value):
        final: Any = None
        async for item in value:
            final = item
        return final
    return value


class FunctionTool(BaseTool):
    def __init__(
        self,
        func: Callable[..., Any] | None = None,
        coroutine: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        args_schema: Type[BaseModel] | None = None,
    ) -> None:
        super().__init__()
        self.func = func
        self.coroutine = coroutine
        target = coroutine or func
        if target is None:
            raise ValueError("FunctionTool requires func or coroutine")
        self.name = name or target.__name__
        self.description = description or inspect.getdoc(target) or ""
        self.args_schema = args_schema or _schema_from_signature(target)

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        target = self.coroutine or self.func
        try:
            sig = inspect.signature(target)
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            if has_varkw:
                filtered_args = arguments
            else:
                valid_keys = set(sig.parameters.keys())
                filtered_args = {k: v for k, v in arguments.items() if k in valid_keys}
        except Exception:
            filtered_args = arguments
        result = target(**filtered_args)
        if inspect.isawaitable(result):
            return await result
        return result


class StructuredTool(FunctionTool):
    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any] | None = None,
        coroutine: Callable[..., Any] | None = None,
        name: str | None = None,
        description: str | None = None,
        args_schema: Type[BaseModel] | None = None,
        **_: Any,
    ) -> "StructuredTool":
        return cls(
            func=func,
            coroutine=coroutine,
            name=name,
            description=description,
            args_schema=args_schema,
        )


def tool(func: Callable[..., Any]) -> FunctionTool:
    return FunctionTool(func=func)
