from __future__ import annotations

import inspect
import asyncio
from typing import Any, Callable, Type

from pydantic import BaseModel, Field, create_model


def _schema_from_signature(func: Callable[..., Any]) -> Type[BaseModel]:
    fields: dict[str, tuple[type[Any], Any]] = {}
    signature = inspect.signature(func)
    for name, param in signature.parameters.items():
        annotation = param.annotation if param.annotation is not inspect._empty else str
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
        result = target(**arguments)
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
