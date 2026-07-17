import json
import logging
import asyncio
import time
import uuid
from typing import Dict, Any, List, Optional, Tuple
import httpx
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from app.core.orm import AsyncSessionLocal
from app.models.mcp import McpServer, McpToolCache
from sqlalchemy import select, update

logger = logging.getLogger(__name__)

class McpSseSession:
    """Manages an MCP connection, supporting both standard SSE and Direct HTTP Post gateways"""
    def __init__(self, server_id: str, sse_url: str, auth_headers: Optional[Dict] = None):
        self.server_id = server_id
        self.sse_url = sse_url
        self.auth_headers = auth_headers or {}
        self.session: Optional[ClientSession] = None
        self.last_used_at = time.time()
        self._lock = asyncio.Lock()
        self._exit_stack = None
        self.is_direct_http = False 
        self.mcp_session_id: Optional[str] = None
        self._rpc_id_counter = 1

    def next_rpc_id(self) -> int:
        self._rpc_id_counter += 1
        return self._rpc_id_counter

    async def connect(self):
        """Establishes connection with protocol detection"""
        async with self._lock:
            if self.session or self.mcp_session_id:
                return

            # Debug: Log header keys (redacted)
            header_keys = list(self.auth_headers.keys())
            logger.info(f"[MCP] Connecting to {self.server_id} at {self.sse_url}. Headers keys present: {header_keys}")
            if "Authorization" in self.auth_headers:
                logger.info(f"[MCP] Auth Value (first 10 chars): {self.auth_headers['Authorization'][:10]}...")

            
            try:
                from contextlib import AsyncExitStack
                self._exit_stack = AsyncExitStack()
                
                # 1. Try Standard SSE Connection
                try:
                    async def _connect_sse():
                        read_stream, write_stream = await self._exit_stack.enter_async_context(
                            sse_client(url=self.sse_url, headers=self.auth_headers)
                        )
                        self.session = await self._exit_stack.enter_async_context(
                            ClientSession(read_stream, write_stream)
                        )
                        await self.session.initialize()

                    await asyncio.wait_for(_connect_sse(), timeout=10.0)
                    
                    self.last_used_at = time.time()
                    self.is_direct_http = False
                    logger.info(f"[MCP] Standard SSE initialized for {self.server_id}")
                    return
                except Exception as sse_err:
                    logger.error(f"[MCP] Standard SSE failed for {self.server_id}. Error: {str(sse_err)}", exc_info=True)
                    await self._exit_stack.aclose()
                    self._exit_stack = AsyncExitStack()

                # 2. Fallback: Direct HTTP Gateway
                self.is_direct_http = True
                logger.info(f"[MCP] Switched to Direct HTTP mode for {self.server_id}")
                self.last_used_at = time.time()
                
            except Exception as e:
                logger.error(f"[MCP] Connection failed for {self.server_id}: {e}", exc_info=True)
                raise

    async def close(self):
        """Closes the connection"""
        async with self._lock:
            exit_stack = self._exit_stack
            self._exit_stack = None
            self.session = None
            self.mcp_session_id = None
            if exit_stack:
                try:
                    await exit_stack.aclose()
                except Exception as exc:
                    logger.warning(
                        "[MCP] Transport cleanup failed for %s; cached state was cleared: %s",
                        self.server_id,
                        exc,
                    )
            logger.info(f"[MCP] Session closed for {self.server_id}")

    def update_activity(self):
        self.last_used_at = time.time()

class McpClientService:
    _sessions: Dict[str, McpSseSession] = {}
    _cleanup_task: Optional[asyncio.Task] = None

    @classmethod
    async def get_session(cls, server_id: str) -> McpSseSession:
        if not cls._cleanup_task:
            cls._cleanup_task = asyncio.create_task(cls._idle_cleanup_loop())

        if server_id not in cls._sessions:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(McpServer).where(McpServer.id == server_id))
                server = result.scalar_one_or_none()
                if not server: raise ValueError(f"MCP Server {server_id} not found")
                headers = json.loads(server.auth_headers) if server.auth_headers else {}
                cls._sessions[server_id] = McpSseSession(server_id, server.sse_url, headers)

        session = cls._sessions[server_id]
        await session.connect()
        session.update_activity()
        return session

    @classmethod
    async def list_remote_tools(cls, server_id: str) -> List[Any]:
        session_mgr = await cls.get_session(server_id)
        
        if not session_mgr.is_direct_http:
            try:
                response = await session_mgr.session.list_tools()
            except Exception as exc:
                logger.warning(
                    "[MCP] Tool listing failed for %s; reconnecting once: %s",
                    server_id,
                    exc,
                )
                await session_mgr.close()
                await session_mgr.connect()
                if session_mgr.is_direct_http:
                    if not session_mgr.mcp_session_id:
                        await cls._initialize_direct_http(session_mgr)
                    res = await cls._direct_http_rpc(session_mgr, "tools/list", {})
                    if isinstance(res, dict) and "tools" in res:
                        return res["tools"]
                    return res if isinstance(res, list) else []
                response = await session_mgr.session.list_tools()
            return response.tools
        else:
            # Direct HTTP Flow: 1. Init -> 2. Initialized -> 3. List
            if not session_mgr.mcp_session_id:
                await cls._direct_http_rpc(session_mgr, "initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "nanzi-ai-agent", "version": "1.0.0"}
                })
                await cls._direct_http_rpc(session_mgr, "notifications/initialized", {}, is_notification=True)
            
            res = await cls._direct_http_rpc(session_mgr, "tools/list", {})
            if isinstance(res, dict) and "tools" in res: return res["tools"]
            elif isinstance(res, list): return res
            return []

    @classmethod
    async def _initialize_direct_http(cls, session_mgr: McpSseSession) -> None:
        await cls._direct_http_rpc(session_mgr, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "nanzi-ai-agent", "version": "1.0.0"},
        })
        await cls._direct_http_rpc(
            session_mgr,
            "notifications/initialized",
            {},
            is_notification=True,
        )

    @classmethod
    async def _recover_direct_http_session(
        cls,
        session_mgr: McpSseSession,
        stale_session_id: Optional[str],
    ) -> None:
        """Reinitialize once, while coalescing concurrent stale-session recovery."""
        async with session_mgr._lock:
            if (
                stale_session_id
                and session_mgr.mcp_session_id
                and session_mgr.mcp_session_id != stale_session_id
            ):
                return
            session_mgr.mcp_session_id = None
            await cls._initialize_direct_http(session_mgr)

    @classmethod
    async def call_remote_tool(cls, server_id: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        session_mgr = await cls.get_session(server_id)
        
        if not session_mgr.is_direct_http:
            try:
                response = await session_mgr.session.call_tool(tool_name, arguments)
                text = "".join(
                    getattr(item, "text", "")
                    for item in (getattr(response, "content", None) or [])
                )
                if bool(getattr(response, "isError", False) or getattr(response, "is_error", False)):
                    raise RuntimeError(text or f"MCP tool '{tool_name}' returned an error")
                structured_content = getattr(response, "structuredContent", None)
                if structured_content is None:
                    structured_content = getattr(response, "structured_content", None)
                if structured_content is not None:
                    return {
                        "success": True,
                        "content": text,
                        "structured_content": structured_content,
                    }
                return text if text else {"success": True, "content": ""}
            except Exception as e:
                await session_mgr.close()
                raise RuntimeError(f"MCP tool '{tool_name}' failed: {e}") from e
        else:
            if not session_mgr.mcp_session_id:
                await cls._direct_http_rpc(session_mgr, "initialize", {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "nanzi", "version": "1.0.0"}
                })
                await cls._direct_http_rpc(session_mgr, "notifications/initialized", {}, is_notification=True)

            res = await cls._direct_http_rpc(session_mgr, "tools/call", {
                "name": tool_name,
                "arguments": arguments
            })
            if isinstance(res, dict) and bool(res.get("isError") or res.get("is_error")):
                error_text = "".join(
                    c.get("text", "")
                    for c in res.get("content") or []
                    if isinstance(c, dict) and c.get("type") == "text"
                )
                raise RuntimeError(
                    error_text
                    or str(res.get("message") or res.get("error") or "")
                    or f"MCP tool '{tool_name}' returned an error"
                )
            if isinstance(res, dict) and "content" in res:
                text = "".join(
                    c.get("text", "")
                    for c in (res.get("content") or [])
                    if isinstance(c, dict) and c.get("type") == "text"
                )
                structured_content = res.get("structuredContent")
                if structured_content is None:
                    structured_content = res.get("structured_content")
                if structured_content is not None:
                    return {
                        "success": True,
                        "content": text,
                        "structured_content": structured_content,
                    }
                return text if text else {"success": True, "content": ""}
            if res is None:
                return {"success": True, "content": ""}
            return str(res)

    @staticmethod
    def _parse_sse_payload(text: str) -> Optional[Dict]:
        """从 Streamable HTTP 的 SSE 响应体中解析出 JSON-RPC payload。

        响应体形如：
            event: message
            data: {"jsonrpc":"2.0","id":3,"result":{...}}

        多行 data: 按 SSE 规范拼接；返回最后一条解析成功的 JSON-RPC 对象。
        """
        if not text:
            return None
        data_buffer: list = []
        events: list = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_buffer.append(line[len("data:"):].lstrip())
            elif line == "" and data_buffer:
                events.append("".join(data_buffer))
                data_buffer = []
        if data_buffer:
            events.append("".join(data_buffer))
        for payload in reversed(events):
            payload = payload.strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict) and ("result" in obj or "error" in obj or "method" in obj):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _is_session_expired_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        error = payload.get("error")
        candidates = [
            payload.get("Code"),
            payload.get("code"),
            payload.get("Message"),
            payload.get("message"),
        ]
        if isinstance(error, dict):
            candidates.extend([error.get("code"), error.get("message")])
        normalized = " ".join(str(value).lower() for value in candidates if value is not None)
        return any(marker in normalized for marker in (
            "sessionexpired",
            "session expired",
            "session not found",
            "unknown session",
            "invalid session",
        ))

    @classmethod
    async def _direct_http_rpc(cls, session_mgr: McpSseSession, method: str, params: Optional[Dict], is_notification: bool = False, retry_count: int = 0) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **session_mgr.auth_headers
        }
        request_session_id = session_mgr.mcp_session_id
        if request_session_id:
            headers["mcp-session-id"] = request_session_id
        
        rpc_id = session_mgr.next_rpc_id() if not is_notification else None
        payload = { "jsonrpc": "2.0", "method": method, "params": params or {} }
        if rpc_id is not None:
            payload["id"] = rpc_id

        logger.debug(f"[MCP-Direct] Request: {method} to {session_mgr.sse_url} | RPC ID: {rpc_id} | Headers keys: {list(headers.keys())}")
        if "Authorization" in headers:
             logger.info(f"[MCP-Direct] Sending Authorization: {headers['Authorization'][:15]}...")


        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(session_mgr.sse_url, json=payload, headers=headers)
                logger.info(f"[MCP-Direct] Response from {method}: HTTP {resp.status_code}")
            except Exception as http_err:
                logger.error(f"[MCP-Direct] HTTP Request failed for {method}: {http_err}")
                raise
            
            # Capture Session ID from initialization
            if method == "initialize" and resp.status_code == 200:
                s_id = resp.headers.get("mcp-session-id")
                if not s_id:
                    try:
                        try:
                            res_data = resp.json().get("result", {})
                        except json.JSONDecodeError:
                            res_data = (cls._parse_sse_payload(resp.text) or {}).get("result", {})
                        s_id = res_data.get("_experimental", {}).get("session_id") or res_data.get("session_id")
                    except:
                        pass
                if s_id: 
                    session_mgr.mcp_session_id = s_id
                    logger.info(f"[MCP-Direct] Captured Session ID: {s_id}")

            # Accept all 2xx codes (200, 201, 202, 204)
            if 200 <= resp.status_code < 300:
                if resp.status_code == 204 or not resp.text:
                    return None
                
                raw_text = resp.text
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    data = cls._parse_sse_payload(raw_text)
                    if data is None:
                        logger.warning(f"[MCP-Direct] Non-JSON success response: {raw_text[:200]}")
                        return None  # Success but not JSON (e.g. 202 Accepted)
                    logger.info(f"[MCP-Direct] Parsed SSE-encoded response for {method}")

                if "result" in data: return data["result"]
                if is_notification: return None
                if "error" in data:
                    if (
                        request_session_id
                        and retry_count < 1
                        and not is_notification
                        and method != "initialize"
                        and cls._is_session_expired_payload(data)
                    ):
                        logger.warning(
                            "[MCP-Direct] Session invalid for %s; reinitializing before retrying %s",
                            session_mgr.server_id,
                            method,
                        )
                        await cls._recover_direct_http_session(session_mgr, request_session_id)
                        return await cls._direct_http_rpc(
                            session_mgr,
                            method,
                            params,
                            is_notification,
                            retry_count=retry_count + 1,
                        )
                    logger.error(f"[MCP-Direct] RPC Error Response: {data['error']}")
                    raise Exception(f"RPC Error {data['error'].get('code')}: {data['error'].get('message')}")
                return data

            error_payload = None
            try:
                error_payload = resp.json()
            except json.JSONDecodeError:
                error_payload = cls._parse_sse_payload(resp.text)
            if (
                request_session_id
                and retry_count < 1
                and not is_notification
                and method != "initialize"
                and resp.status_code in {400, 401, 404, 410}
                and cls._is_session_expired_payload(error_payload)
            ):
                logger.warning(
                    "[MCP-Direct] HTTP %s invalidated session for %s; reinitializing before retrying %s",
                    resp.status_code,
                    session_mgr.server_id,
                    method,
                )
                await cls._recover_direct_http_session(session_mgr, request_session_id)
                return await cls._direct_http_rpc(
                    session_mgr,
                    method,
                    params,
                    is_notification,
                    retry_count=retry_count + 1,
                )

            logger.error(f"[MCP-Direct] Error Response Body: {resp.text[:500]}")
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")

    @classmethod
    async def sync_tools(cls, server_id: str):
        tools = await cls.list_remote_tools(server_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(McpServer).where(McpServer.id == server_id))
            server = result.scalar_one_or_none()
            if not server: return
            from datetime import datetime
            server.enabled_status = 1
            server.last_sync_at = datetime.now()
            for t in tools:
                t_name = t.name if hasattr(t, 'name') else t.get('name')
                t_desc = t.description if hasattr(t, 'description') else t.get('description')
                t_schema = t.inputSchema if hasattr(t, 'inputSchema') else t.get('inputSchema', t.get('parameter_schema'))
                if hasattr(t_schema, "model_dump"):
                    t_schema = t_schema.model_dump(mode="json")
                schema_payload = dict(t_schema or {})
                annotations = getattr(t, "annotations", None)
                if annotations is None and isinstance(t, dict):
                    annotations = t.get("annotations")
                if hasattr(annotations, "model_dump"):
                    annotations = annotations.model_dump(mode="json")
                if isinstance(annotations, dict) and annotations:
                    schema_payload["x-nanzi-mcp-annotations"] = annotations
                full_name = f"{server.server_name}:{t_name}"
                stmt = select(McpToolCache).where(McpToolCache.server_id == server_id, McpToolCache.tool_name == full_name)
                existing = (await db.execute(stmt)).scalar_one_or_none()
                if existing:
                    existing.tool_description = t_desc
                    existing.parameter_schema = json.dumps(schema_payload)
                else:
                    db.add(McpToolCache(id=str(uuid.uuid4()), server_id=server_id, tool_name=full_name, tool_description=t_desc, parameter_schema=json.dumps(schema_payload), is_published=False))
            await db.commit()

    @classmethod
    async def _idle_cleanup_loop(cls):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            for sid, session in cls._sessions.items():
                if (session.session or session.mcp_session_id) and (now - session.last_used_at > 300):
                    await session.close()

    @classmethod
    async def shutdown(cls):
        if cls._cleanup_task: cls._cleanup_task.cancel()
        for session in cls._sessions.values(): await session.close()
