import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Any
import httpx

from app.core.errors import (
    RagFlowError,
    RagFlowServiceError,
    RagFlowTransientError,
    RagFlowUnavailableError,
)
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

# 超时策略
# RAGFlow 的元数据接口在本平台上实测可达 5s+，写操作（删除 / 解析）还要在它
# 内部清理向量库索引，检索更要等 embedding 模型返回；超时给得太紧会把 RAGFlow
# 的真实错误掩盖成客户端超时（对方还在重试，我们已经放弃），排查时只能看到
# 一行空的 ``httpx.ReadTimeout``。
DEFAULT_METADATA_TIMEOUT = 20.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_RETRIEVE_TIMEOUT = 30.0


def _wrap_transport_error(
    exc: httpx.TransportError, action: str, timeout_val: float
) -> RagFlowError:
    """把 httpx 的网络层异常翻译成带排查线索的 RagFlowError。

    ``str(httpx.ReadTimeout())`` 是空字符串，所以这里显式带上异常类型，
    并针对超时补上「RAGFlow 很可能在等它依赖的模型服务」这一常见根因。
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return RagFlowTransientError(
            f"RAGFlow {action} 无法连接（{type(exc).__name__}）：{exc!r}。"
            "请确认 RAGFlow 服务地址可达。"
        )
    if isinstance(exc, httpx.TimeoutException):
        return RagFlowUnavailableError(
            f"RAGFlow {action} 超时（{timeout_val:g}s，{type(exc).__name__}）。"
            "RAGFlow 可能正在等待它依赖的服务（向量库 / embedding 模型）返回，"
            "请检查 RAGFlow 的模型提供商配置与连通性。"
        )
    return RagFlowUnavailableError(
        f"RAGFlow {action} 网络异常（{type(exc).__name__}）：{exc!r}。"
    )


class RagFlowClient:
    """
    Client for interacting with RAGFlow OpenAPI.
    Handles authentication, conversation management, and streaming responses.
    """

    def __init__(
        self, 
        config_prefix: str = "ragflow",
        override_url: Optional[str] = None,
        override_key: Optional[str] = None
    ):
        self.base_url: Optional[str] = override_url
        if self.base_url and self.base_url.endswith("/"):
            self.base_url = self.base_url[:-1]
        self.api_key: Optional[str] = override_key
        self.config_prefix: str = config_prefix

    async def _ensure_config(self):
        """Lazy load configuration"""
        if not self.base_url:
            self.base_url = await ConfigService.get(f"{self.config_prefix}_api_url")
            if self.base_url and self.base_url.endswith("/"):
                self.base_url = self.base_url[:-1]
        
        if not self.api_key:
            self.api_key = await ConfigService.get(f"{self.config_prefix}_api_key")
            
        if not self.base_url or not self.api_key:
            raise ValueError(f"RAGFlow configuration ({self.config_prefix}_api_url, {self.config_prefix}_api_key) is missing.")

    def _translate_error(self, error_msg: str, action: str) -> str:
        """Translate raw RAGFlow error messages into friendly Chinese tips"""
        msg = (error_msg or "").strip()
        if "lacks permission for datasets" in msg:
            return "RAGFlow 权限拒绝：当前配置的 API Key 无权操作该知识库。该知识库可能属于其他用户或团队空间，请联系管理员确认授权状态，或直接在 RAGFlow 控制台操作。"
        if "lacks permission for documents" in msg:
            return "RAGFlow 权限拒绝：当前配置的 API Key 无权操作该文档。该文档可能属于其他用户，请确认权限。"
        if "Dataset not found" in msg or "dataset not found" in msg:
            return "RAGFlow 侧未找到该知识库，可能已被物理删除。"
        if "Document not found" in msg or "document not found" in msg:
            return "RAGFlow 侧未找到该文档，可能已被物理删除。"
        if "ModelException" in msg or "Connection error" in msg or "APIConnectionError" in msg:
            return (
                "RAGFlow 的模型服务连接失败：RAGFlow 无法访问它配置的模型提供商"
                "（embedding / LLM）。常见原因是 Base URL 或 API Key 失效、"
                f"模型服务宕机或网络不通。原始错误: {msg}"
            )
        return f"RAGFlow {action} 错误: {msg}"

    async def _handle_response(self, response: httpx.Response, action: str) -> Dict[str, Any]:
        """Centralized response handling"""
        if response.status_code != 200:
            detail = response.text.strip() or f"HTTP {response.status_code} {response.reason_phrase}"
            logger.error(f"[RAGFlow] {action} Failed ({response.status_code}): {detail}")
            friendly_detail = self._translate_error(detail, action)
            raise RagFlowServiceError(friendly_detail)
        
        res_json = response.json()
        if res_json.get("code") != 0:
            msg = res_json.get('message') or ""
            logger.error(f"[RAGFlow] {action} Service Error: {msg}")
            friendly_detail = self._translate_error(msg, action)
            raise RagFlowServiceError(friendly_detail)
        
        return res_json.get("data", {})

    async def _request(
        self,
        method: str,
        url: str,
        *,
        action: str,
        timeout: float,
        **kwargs: Any,
    ) -> Any:
        """统一请求入口：把网络层异常包装成 RagFlowError 并记录异常类型。

        httpx 的异常字符串为空，所以日志里显式打印 ``type(e).__name__``，
        否则只会留下一条 ``... failed: `` 这样无法排查的记录。
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, **kwargs)
                return await self._handle_response(resp, action)
        except httpx.TransportError as e:
            wrapped = _wrap_transport_error(e, action, timeout)
            logger.error(
                f"[RAGFlow] {action} 网络失败: {type(e).__name__}: {e!r} -> {wrapped}"
            )
            raise wrapped from e

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    # --- Dataset Management ---

    async def list_datasets(self, name: Optional[str] = None, page: int = 1, page_size: int = 100) -> List[Dict[str, Any]]:
        """List datasets, optionally filtering by name (Client-side filtering due to API permission issues)"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets"
        
        # RAGFlow limits page_size to <= 100. If larger, we fetch multiple pages of max size 100
        if page_size <= 100:
            params = {"page": page, "page_size": page_size}
            async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
                data = await self._handle_response(resp, "List Datasets")
                
                if isinstance(data, list):
                    datasets = data
                elif isinstance(data, dict):
                    datasets = data.get("datasets") or data.get("items") or data.get("data") or []
                else:
                    datasets = []
        else:
            datasets = []
            start_offset = (page - 1) * page_size
            end_offset = start_offset + page_size
            
            ragflow_page_size = 100
            start_page = (start_offset // ragflow_page_size) + 1
            end_page = ((end_offset - 1) // ragflow_page_size) + 1
            
            async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
                for p in range(start_page, end_page + 1):
                    params = {"page": p, "page_size": ragflow_page_size}
                    resp = await client.get(url, headers=self._get_headers(), params=params)
                    data = await self._handle_response(resp, f"List Datasets Page {p}")
                    if isinstance(data, list):
                        page_data = data
                    elif isinstance(data, dict):
                        page_data = data.get("datasets") or data.get("items") or data.get("data") or []
                    else:
                        page_data = []
                    
                    if not page_data:
                        break
                    datasets.extend(page_data)
                    if len(page_data) < ragflow_page_size:
                        break
            
            slice_start = start_offset - (start_page - 1) * ragflow_page_size
            slice_end = slice_start + page_size
            datasets = datasets[slice_start:slice_end]

        # Client-side filtering
        if name:
            datasets = [d for d in datasets if d.get("name") == name]
            
        return datasets

    async def create_dataset(self, name: str, description: str = "", chunk_method: str = "naive") -> Dict[str, Any]:
        """Create a new dataset (Knowledge Base)"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets"
        payload = {
            "name": name,
            "description": description,
            "chunk_method": chunk_method,
            "permission": "me"
        }
        
        # If chunk_method is 'one', we don't need complex parser_config
        if chunk_method == "naive":
            # RAGFlow API: layout_recognize must be a string (e.g. "DeepDOC"), not bool
            payload["parser_config"] = {
                "chunk_token_num": 2048,
                "layout_recognize": "DeepDOC",
            }

        async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            return await self._handle_response(resp, "Create Dataset")

    async def delete_datasets(self, dataset_ids: List[str]):
        """Delete datasets by IDs"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets"
        payload = {"ids": dataset_ids}
        
        async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
            resp = await client.request("DELETE", url, headers=self._get_headers(), json=payload)
            await self._handle_response(resp, "Delete Datasets")

    # --- Document Management ---

    async def list_documents(self, dataset_id: str, name: Optional[str] = None, page: int = 1, page_size: int = 100) -> List[Dict[str, Any]]:
        """List documents in a dataset"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents"
        
        # RAGFlow limits page_size to <= 100. If larger, we fetch multiple pages of max size 100
        if page_size <= 100:
            params = {"page": page, "page_size": page_size}
            if name:
                params["name"] = name
                
            async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
                data = await self._handle_response(resp, "List Documents")
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("docs") or data.get("documents") or data.get("data") or []
                return []
        else:
            documents = []
            start_offset = (page - 1) * page_size
            end_offset = start_offset + page_size
            
            ragflow_page_size = 100
            start_page = (start_offset // ragflow_page_size) + 1
            end_page = ((end_offset - 1) // ragflow_page_size) + 1
            
            async with httpx.AsyncClient(timeout=DEFAULT_METADATA_TIMEOUT) as client:
                for p in range(start_page, end_page + 1):
                    params = {"page": p, "page_size": ragflow_page_size}
                    if name:
                        params["name"] = name
                    resp = await client.get(url, headers=self._get_headers(), params=params)
                    data = await self._handle_response(resp, f"List Documents Page {p}")
                    if isinstance(data, list):
                        page_data = data
                    elif isinstance(data, dict):
                        page_data = data.get("docs") or data.get("documents") or data.get("data") or []
                    else:
                        page_data = []
                        
                    if not page_data:
                        break
                    documents.extend(page_data)
                    if len(page_data) < ragflow_page_size:
                        break
            
            slice_start = start_offset - (start_page - 1) * ragflow_page_size
            slice_end = slice_start + page_size
            return documents[slice_start:slice_end]

    async def upload_document(
        self,
        dataset_id: str,
        file_name: str,
        blob: bytes,
        content_type: str = "application/octet-stream"
    ) -> Dict[str, Any]:
        """Upload a file to dataset"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents"
        
        # multipart/form-data doesn't use standard json headers
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (file_name, blob, content_type or "application/octet-stream")}
        
        async with httpx.AsyncClient(timeout=DEFAULT_WRITE_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, files=files)
            # Returns a list of uploaded docs in data
            data = await self._handle_response(resp, "Upload Document")
            return data[0] if isinstance(data, list) and len(data) > 0 else data

    async def delete_documents(self, dataset_id: str, document_ids: List[str]):
        """Delete documents from dataset"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents"
        payload = {"ids": document_ids}
        
        # Note: The API spec shows DELETE /api/v1/datasets/{dataset_id}/documents takes Body with ids
        # We need to verify if httpx.delete supports body or we use client.request
        # RAGFlow 侧要同步清理向量库中的 chunk，比普通 REST 调用慢，超时按写操作给。
        await self._request(
            "DELETE",
            url,
            action="Delete Documents",
            timeout=DEFAULT_WRITE_TIMEOUT,
            headers=self._get_headers(),
            json=payload,
        )

    async def parse_documents(self, dataset_id: str, document_ids: List[str]):
        """Trigger parsing for documents"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/chunks"
        payload = {"document_ids": document_ids}
        
        await self._request(
            "POST",
            url,
            action="Parse Documents",
            timeout=DEFAULT_WRITE_TIMEOUT,
            headers=self._get_headers(),
            json=payload,
        )

    async def list_document_chunks(
        self,
        dataset_id: str,
        document_id: str,
        page: int = 1,
        page_size: int = 30
    ) -> Dict[str, Any]:
        """List chunks of a document"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks"
        
        # RAGFlow limits page_size to <= 100. If larger, we fetch multiple pages of max size 100
        if page_size <= 100:
            params = {"page": page, "page_size": page_size}
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
                return await self._handle_response(resp, "List Chunks")
        else:
            merged_chunks = []
            doc_meta = {}
            start_offset = (page - 1) * page_size
            end_offset = start_offset + page_size
            
            ragflow_page_size = 100
            start_page = (start_offset // ragflow_page_size) + 1
            end_page = ((end_offset - 1) // ragflow_page_size) + 1
            
            async with httpx.AsyncClient(timeout=20.0) as client:
                for p in range(start_page, end_page + 1):
                    params = {"page": p, "page_size": ragflow_page_size}
                    resp = await client.get(url, headers=self._get_headers(), params=params)
                    data = await self._handle_response(resp, f"List Chunks Page {p}")
                    
                    if isinstance(data, dict):
                        page_chunks = data.get("chunks") or []
                        if not doc_meta and "doc" in data:
                            doc_meta = data["doc"]
                    else:
                        page_chunks = []
                        
                    if not page_chunks:
                        break
                    merged_chunks.extend(page_chunks)
                    if len(page_chunks) < ragflow_page_size:
                        break
            
            slice_start = start_offset - (start_page - 1) * ragflow_page_size
            slice_end = slice_start + page_size
            return {
                "chunks": merged_chunks[slice_start:slice_end],
                "doc": doc_meta
            }

    async def chat_stream(
        self, 
        query: str, 
        conversation_id: str, 
        history: List[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream chat response from RAGFlow.
        
        Args:
            query: The user's question.
            conversation_id: ID for the session (mapped to RAGFlow's conversation_id).
            history: Optional list of previous messages (if stateless).
            config: Engine config containing 'app_id' (REQUIRED for RAGFlow).
        
        Yields:
            Standardized chat chunks.
        """
        await self._ensure_config()
        
        if not config or "app_id" not in config:
            yield {
                "content": "⚠️ **Configuration Error**: Missing `app_id` in Agent Engine Config.\nPlease contact administrator.",
                "type": "error"
            }
            return

        app_id = config["app_id"]
        url = f"{self.base_url}/api/v1/agents_openai/{app_id}/chat/completions"
        
        # Construct OpenAI-compatible Payload
        # NOTE: We remove 'session_id' because RAGFlow's OpenAI endpoint throws 'Session not found' 
        # if the ID doesn't exist server-side. We rely on passing full 'messages' history (Stateless mode).
        payload = {
            "model": "ragflow", # Required but ignored
            "messages": history or [{"role": "user", "content": query}],
            "stream": True
        }
        
        # Optional: Add top_p, temperature if present in config
        if "temperature" in config:
            payload["temperature"] = config["temperature"]

        logger.info(f"[RAGFlow OpenAI-Compatible] Request: {url}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                async with client.stream("POST", url, headers=self._get_headers(), json=payload) as response:
                    if response.status_code != 200:
                        err_text = await response.read()
                        yield {
                            "content": f"**RAGFlow Error** ({response.status_code}): {err_text.decode()}",
                            "type": "error"
                        }
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            
                            try:
                                data = json.loads(data_str)
                                
                                # OpenAI Format Parsing
                                if "choices" in data and len(data["choices"]) > 0:
                                    choice = data["choices"][0]
                                    delta = choice.get("delta", {})
                                    
                                    # 1. Answer Content
                                    if "content" in delta:
                                        yield {
                                            "content": delta["content"],
                                            "type": "answer"
                                        }
                                    
                                    # 2. Citations/References (Deep extraction)
                                    # RAGFlow can put this in delta.reference, choice.reference, or even data.reference
                                    ref_data = (
                                        delta.get("reference") or 
                                        choice.get("reference") or 
                                        data.get("reference") or
                                        choice.get("citations")
                                    )
                                    
                                    if ref_data:
                                        raw_chunks = []
                                        if isinstance(ref_data, dict):
                                            # Normalize dictionary keyed by ID (common in RAGFlow)
                                            chunks_obj = ref_data.get("chunks", {})
                                            if isinstance(chunks_obj, dict):
                                                for cid, cinfo in chunks_obj.items():
                                                    if isinstance(cinfo, dict):
                                                        cinfo["id"] = str(cid) # Use key as ID for matching
                                                        raw_chunks.append(cinfo)
                                            elif isinstance(chunks_obj, list):
                                                raw_chunks = chunks_obj
                                        
                                        normalized_refs = []
                                        for ref in raw_chunks:
                                            # Priority name fields for RAGFlow documents
                                            doc_name = (
                                                ref.get("document_name") or 
                                                ref.get("document_keyword") or 
                                                ref.get("doc_name") or 
                                                ref.get("docnm_kwd") or 
                                                "Unknown Document"
                                            )
                                            
                                            normalized_refs.append({
                                                "id": ref.get("id"),
                                                "doc_name": doc_name,
                                                "content": ref.get("content") or ref.get("content_with_weight") or "",
                                                "similarity": ref.get("similarity", 0.0),
                                                "chunk_id": ref.get("chunk_id") or ref.get("id")
                                            })
                                        
                                        if normalized_refs:
                                            yield {
                                                "type": "citation",
                                                "data": normalized_refs
                                            }
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"RAGFlow Stream Error: {type(e).__name__}: {e!r}")
                yield {
                    "content": f"**Connection Error**: {type(e).__name__}: {e!r}",
                    "type": "error"
                }

    async def retrieve(
        self, 
        query: str, 
        dataset_ids: List[str], 
        top_k: int = 5,
        similarity_threshold: float = 0.5,
        vector_similarity_weight: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Retrieve chunks from specific datasets with robust auto-retry on network errors or 5xx failures.
        
        Args:
            query: User question.
            dataset_ids: List of dataset IDs.
            top_k: Number of chunks to retrieve.
            similarity_threshold: Minimum similarity score.
            vector_similarity_weight: Weight for vector search (0-1). Remaining is full-text weight.
        """
        await self._ensure_config()
        
        url = f"{self.base_url}/api/v1/retrieval"
        payload = {
            "question": query,
            "dataset_ids": dataset_ids,
            "top_k": top_k,
            "page_size": top_k * 3, # 增大候选池深度，提高重排稳定性
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight
        }
        
        # [Log] Request Payload
        logger.info(f"[RAGFlowClient] Retrieval Request Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        max_attempts = 2
        last_exception: Optional[BaseException] = None
        timeout_val = await self._resolve_retrieve_timeout()

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_val) as client:
                    response = await client.post(url, headers=self._get_headers(), json=payload)
                    
                    if response.status_code == 200:
                        res_json = response.json()
                        
                        if res_json.get("code") == 0:
                            chunks = res_json.get("data", [])
                            # Normalize chunks
                            if isinstance(chunks, dict):
                                chunks = chunks.get("chunks", [])
                            
                            # [Log] Response Summary
                            logger.info(f"[RAGFlowClient] Retrieval Success. Found {len(chunks) if chunks else 0} chunks. (Query: {query[:20]}...)")
                            
                            normalized_chunks = []
                            if isinstance(chunks, list):
                                for chunk in chunks:
                                    # Robust document name extraction
                                    doc_name = (
                                        chunk.get("document_keyword") or
                                        chunk.get("doc_name") or 
                                        chunk.get("docnm_kwd") or 
                                        chunk.get("doc_nm") or 
                                        chunk.get("document_name") or 
                                        chunk.get("source") or 
                                        "Unknown Document"
                                    )
                                    
                                    if doc_name == "Unknown Document":
                                         logger.debug(f"[RAGFlow] Missing doc_name in retrieval chunk: {json.dumps(chunk, ensure_ascii=False)}")
 
                                    positions = chunk.get("positions")
                                    page_no = chunk.get("page_no") or chunk.get("page_num")
                                    if not page_no and isinstance(positions, list) and len(positions) > 0:
                                        first_pos = positions[0]
                                        if isinstance(first_pos, list) and len(first_pos) > 0:
                                            page_no = first_pos[0]
                                        elif isinstance(first_pos, dict):
                                            page_no = first_pos.get("page_no") or first_pos.get("page_num")

                                    normalized_chunks.append({
                                        "doc_name": doc_name,
                                        "content": chunk.get("content_with_weight") or chunk.get("content") or str(chunk),
                                        "similarity": chunk.get("similarity", 0.0),
                                        "chunk_id": chunk.get("chunk_id") or chunk.get("id"),
                                        "doc_id": chunk.get("doc_id") or chunk.get("document_id"),
                                        "dataset_id": chunk.get("dataset_id"),
                                        "positions": positions,
                                        "page_no": page_no
                                    })
                            return normalized_chunks
                        else:
                            raw_msg = str(res_json.get("message") or "")
                            logger.error(
                                f"[RAGFlow] Retrieval Service Error (code={res_json.get('code')}): {raw_msg}"
                            )
                            raise RagFlowServiceError(self._translate_error(raw_msg, "Retrieval"))
                    elif response.status_code >= 500:
                        raw_msg = response.text.strip() or f"HTTP {response.status_code} {response.reason_phrase}"
                        logger.error(f"[RAGFlow] Retrieval HTTP Error {response.status_code}: {raw_msg}")
                        raise RagFlowTransientError(
                            f"RAGFlow Retrieval 上游错误 {response.status_code}: {raw_msg}"
                        )
                    else:
                        raw_msg = response.text.strip() or f"HTTP {response.status_code} {response.reason_phrase}"
                        logger.error(f"[RAGFlow] Retrieval HTTP Error {response.status_code}: {raw_msg}")
                        raise RagFlowServiceError(self._translate_error(raw_msg, "Retrieval"))
            except httpx.TransportError as e:
                # httpx 异常 str() 为空，必须显式记录异常类型，否则日志只剩一行空白。
                wrapped = _wrap_transport_error(e, "Retrieval", timeout_val)
                last_exception = wrapped
                logger.warning(
                    f"[RAGFlow] Retrieval attempt {attempt} failed: "
                    f"{type(e).__name__}: {e!r} -> {wrapped}"
                )
                if not wrapped.retryable:
                    break
            except RagFlowError as e:
                last_exception = e
                logger.warning(
                    f"[RAGFlow] Retrieval attempt {attempt} failed: {type(e).__name__}: {e}"
                )
                if not e.retryable:
                    break
            except Exception as e:
                last_exception = e
                logger.warning(
                    f"[RAGFlow] Retrieval attempt {attempt} failed: {type(e).__name__}: {e!r}"
                )

            if attempt < max_attempts:
                await asyncio.sleep(attempt * 0.5)

        if last_exception is None:  # pragma: no cover - 循环内必然产生异常
            last_exception = RagFlowUnavailableError("RAGFlow Retrieval 失败：未知原因")
        logger.error(
            f"[RAGFlow] Retrieval failed after {max_attempts} attempt(s) "
            f"(timeout={timeout_val:g}s). Last exception: "
            f"{type(last_exception).__name__}: {last_exception}"
        )
        raise last_exception

    async def _resolve_retrieve_timeout(self) -> float:
        """检索超时：优先取系统配置 ``<prefix>_retrieve_timeout``，否则用默认值。"""
        try:
            cfg_timeout = await ConfigService.get(f"{self.config_prefix}_retrieve_timeout")
            if cfg_timeout:
                return float(cfg_timeout)
        except Exception as e:
            logger.warning(
                f"[RAGFlow] 读取 {self.config_prefix}_retrieve_timeout 失败，"
                f"使用默认值 {DEFAULT_RETRIEVE_TIMEOUT:g}s: {type(e).__name__}: {e!r}"
            )
        return DEFAULT_RETRIEVE_TIMEOUT

    async def download_document(
        self,
        document_id: str,
        *,
        dataset_id: str | None = None,
    ) -> tuple[bytes, str, str]:
        """
        Download document binary content from RAGFlow.
        Prefer dataset-scoped API; falls back to legacy /document/get when dataset_id is omitted.
        Returns: (content, filename, content_type)
        """
        await self._ensure_config()
        if dataset_id:
            url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}"
        else:
            url = f"{self.base_url}/api/v1/document/get/{document_id}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "application/octet-stream")
                # Dataset download returns raw file bytes; JSON means business error.
                if "application/json" in content_type.lower():
                    try:
                        payload = resp.json()
                    except Exception:
                        payload = {}
                    message = payload.get("message") if isinstance(payload, dict) else None
                    raise Exception(
                        f"RAGFlow document download failed: {message or resp.text[:200]}"
                    )

                content_disposition = resp.headers.get("content-disposition", "")
                filename = "document"
                if "filename=" in content_disposition:
                    import urllib.parse
                    raw_fn = content_disposition.split("filename=")[-1].strip('"')
                    filename = urllib.parse.unquote(raw_fn)

                return resp.content, filename, content_type
            raise Exception(f"RAGFlow document download failed ({resp.status_code}): {resp.text}")

