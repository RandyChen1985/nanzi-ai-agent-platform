import asyncio
import httpx
import json

async def test_ragflow_retrieval(url, api_key, dataset_ids, query):
    """
    Test script to verify retrieving chunks from RAGFlow via POST /api/v1/retrieval.
    """
    base_url = url.rstrip("/")
    retrieval_url = f"{base_url}/api/v1/retrieval"
    
    print(f"🔍 Testing RAGFlow Retrieval")
    print(f"📡 URL: {retrieval_url}")
    print(f"📚 Dataset IDs: {dataset_ids}")
    print(f"❓ Query: {query}")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "question": query,
        "dataset_ids": dataset_ids,
        "page": 1,
        "page_size": 10,
        "similarity_threshold": 0.1,  # Very low threshold for testing
        "vector_similarity_weight": 0.3, # Adjust weights if supported
        "top_k": 1024
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(retrieval_url, headers=headers, json=payload)
            print(f"   -> Status Code: {resp.status_code}")
            
            if resp.status_code == 200:
                data = resp.json()
                print(f"   -> Response Raw: {json.dumps(data, ensure_ascii=False, indent=2)}")
                
                if data.get("code") == 0:
                     chunks = data.get("data", {}).get("chunks", []) if isinstance(data.get("data"), dict) else data.get("data", [])
                     print(f"   ✅ SUCCESS! Found {len(chunks)} chunks.")
                else:
                     print(f"   ⚠️ API returned error code: {data.get('code')}")
            else:
                print(f"   ❌ Failed: {resp.status_code}")
                print(f"   Response: {resp.text}")
                
        except Exception as e:
            print(f"   ❌ Exception: {str(e)}")

if __name__ == "__main__":
    RAGFLOW_URL = "http://175.102.130.108"
    RAGFLOW_KEY = "ragflow-QHX513dmfqJunoOZOKGq8hJAAxKOqeHmqzuXG7oyTcY"
    DATASET_IDS = ["32fb6f6eeb7a11f0a4530242ac120006"]
    QUERY = "机房列表"
    
    asyncio.run(test_ragflow_retrieval(RAGFLOW_URL, RAGFLOW_KEY, DATASET_IDS, QUERY))
