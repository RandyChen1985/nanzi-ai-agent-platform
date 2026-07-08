import json
import logging
from typing import AsyncGenerator, Dict, List, Optional, Any
import httpx

from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

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

    async def _handle_response(self, response: httpx.Response, action: str) -> Dict[str, Any]:
        """Centralized response handling"""
        if response.status_code != 200:
            detail = response.text.strip() or f"HTTP {response.status_code} {response.reason_phrase}"
            logger.error(f"[RAGFlow] {action} Failed ({response.status_code}): {detail}")
            raise Exception(f"RAGFlow {action} failed: {detail}")
        
        res_json = response.json()
        if res_json.get("code") != 0:
            logger.error(f"[RAGFlow] {action} Service Error: {res_json.get('message')}")
            raise Exception(f"RAGFlow {action} error: {res_json.get('message')}")
        
        return res_json.get("data", {})

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
            async with httpx.AsyncClient(timeout=10.0) as client:
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
            
            async with httpx.AsyncClient(timeout=10.0) as client:
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

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            return await self._handle_response(resp, "Create Dataset")

    async def delete_datasets(self, dataset_ids: List[str]):
        """Delete datasets by IDs"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets"
        payload = {"ids": dataset_ids}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
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
                
            async with httpx.AsyncClient(timeout=10.0) as client:
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
            
            async with httpx.AsyncClient(timeout=10.0) as client:
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
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, files=files)
            # Returns a list of uploaded docs in data
            data = await self._handle_response(resp, "Upload Document")
            return data[0] if isinstance(data, list) and len(data) > 0 else data

    async def delete_documents(self, dataset_id: str, document_ids: List[str]):
        """Delete documents from dataset"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/documents"
        payload = {"ids": document_ids}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Note: The API spec shows DELETE /api/v1/datasets/{dataset_id}/documents takes Body with ids
            # We need to verify if httpx.delete supports body or we use client.request
            resp = await client.request("DELETE", url, headers=self._get_headers(), json=payload)
            await self._handle_response(resp, "Delete Documents")

    async def parse_documents(self, dataset_id: str, document_ids: List[str]):
        """Trigger parsing for documents"""
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/datasets/{dataset_id}/chunks"
        payload = {"document_ids": document_ids}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=self._get_headers(), json=payload)
            await self._handle_response(resp, "Parse Documents")

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
                logger.error(f"RAGFlow Stream Error: {e}")
                yield {
                    "content": f"**Connection Error**: {str(e)}",
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
        last_exception = None
        
        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
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
                            error_msg = f"RAGFlow API Error ({response.status_code}): {res_json.get('message')}"
                            logger.error(f"[RAGFlow] {error_msg}")
                            raise Exception(error_msg)
                    else:
                        error_msg = f"RAGFlow HTTP Error {response.status_code}: {response.text}"
                        logger.error(f"[RAGFlow] {error_msg}")
                        raise Exception(error_msg)
            except Exception as e:
                last_exception = e
                logger.warning(f"[RAGFlow] Retrieval attempt {attempt} failed: {e}")
                if attempt < max_attempts:
                    import asyncio
                    await asyncio.sleep(attempt * 0.5)
                    continue
        
        # All attempts failed
        logger.error(f"[RAGFlow] All {max_attempts} retrieval attempts failed. Last exception: {last_exception}")
        raise last_exception

    async def download_document(self, document_id: str) -> tuple[bytes, str, str]:
        """
        Download document binary content from RAGFlow.
        Returns: (content, filename, content_type)
        """
        await self._ensure_config()
        url = f"{self.base_url}/api/v1/document/get/{document_id}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                content_disposition = resp.headers.get("content-disposition", "")
                filename = "document"
                if "filename=" in content_disposition:
                    # 解密文件名
                    import urllib.parse
                    raw_fn = content_disposition.split("filename=")[-1].strip('"')
                    filename = urllib.parse.unquote(raw_fn)
                
                content_type = resp.headers.get("content-type", "application/octet-stream")
                return resp.content, filename, content_type
            else:
                raise Exception(f"RAGFlow document download failed ({resp.status_code}): {resp.text}")

