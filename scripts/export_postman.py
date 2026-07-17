#!/usr/bin/env python3
"""
Export OpenAPI schema to Postman Collection

Usage:
    python3 scripts/export_postman.py
"""

import json
import requests
import sys
from datetime import datetime

def convert_openapi_to_postman(openapi_schema):
    """Convert OpenAPI schema to Postman Collection v2.1"""
    
    collection = {
        "info": {
            "name": openapi_schema.get("info", {}).get("title", "API Collection"),
            "description": openapi_schema.get("info", {}).get("description", ""),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "_exporter_id": "nanzi-api-exporter"
        },
        "item": [],
        "auth": {
            "type": "apikey",
            "apikey": [
                {
                    "key": "key",
                    "value": "X-API-Key",
                    "type": "string"
                },
                {
                    "key": "value",
                    "value": "{{api_key}}",
                    "type": "string"
                },
                {
                    "key": "in",
                    "value": "header",
                    "type": "string"
                }
            ]
        },
        "variable": [
            {
                "key": "base_url",
                "value": "http://localhost:8001",
                "type": "string"
            },
            {
                "key": "api_key",
                "value": "your_api_key_here",
                "type": "string"
            }
        ]
    }
    
    # Group by tags
    tag_groups = {}
    
    for path, path_item in openapi_schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.upper() not in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
                continue
            
            # Get tags
            tags = operation.get("tags", ["default"])
            tag_name = tags[0] if tags else "default"
            
            if tag_name not in tag_groups:
                tag_groups[tag_name] = {
                    "name": tag_name,
                    "item": []
                }
            
            # Build request
            request_item = {
                "name": operation.get("summary", path),
                "request": {
                    "method": method.upper(),
                    "header": [],
                    "url": {
                        "raw": "{{base_url}}" + path,
                        "host": ["{{base_url}}"],
                        "path": path.strip("/").split("/")
                    },
                    "description": operation.get("description", "")
                }
            }
            
            # Add request body if present
            if "requestBody" in operation:
                content = operation["requestBody"].get("content", {})
                if "application/json" in content:
                    schema = content["application/json"].get("schema", {})
                    example = content["application/json"].get("example", {})
                    request_item["request"]["body"] = {
                        "mode": "raw",
                        "raw": json.dumps(example or {}, indent=2),
                        "options": {
                            "raw": {
                                "language": "json"
                            }
                        }
                    }
                    request_item["request"]["header"].append({
                        "key": "Content-Type",
                        "value": "application/json"
                    })
            
            # Add security headers
            if operation.get("security"):
                for security_req in operation["security"]:
                    if "X-API-Key" in security_req:
                        request_item["request"]["header"].append({
                            "key": "X-API-Key",
                            "value": "{{api_key}}",
                            "type": "text"
                        })
            
            tag_groups[tag_name]["item"].append(request_item)
    
    # Add all tag groups to collection
    collection["item"] = list(tag_groups.values())
    
    return collection

def main():
    print("=" * 60)
    print("  导出 Postman Collection")
    print("=" * 60)
    
    # Get OpenAPI schema
    print("\n1. 获取 OpenAPI Schema...")
    try:
        response = requests.get("http://localhost:8001/openapi.json")
        response.raise_for_status()
        openapi_schema = response.json()
        print(f"   ✅ 成功获取 OpenAPI 3.0 Schema")
        print(f"   📄 API 标题: {openapi_schema.get('info', {}).get('title')}")
        print(f"   📄 API 版本: {openapi_schema.get('info', {}).get('version')}")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        print("\n   请确保 API 服务正在运行：")
        print("   uvicorn app.main:app --host 0.0.0.0 --port 8001")
        sys.exit(1)
    
    # Convert to Postman
    print("\n2. 转换为 Postman Collection 格式...")
    try:
        postman_collection = convert_openapi_to_postman(openapi_schema)
        total_requests = sum(len(folder["item"]) for folder in postman_collection["item"])
        print(f"   ✅ 转换成功")
        print(f"   📁 分组数: {len(postman_collection['item'])}")
        print(f"   🔗 接口数: {total_requests}")
    except Exception as e:
        print(f"   ❌ 转换失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Save to file
    output_file = "nanzi_api.postman_collection.json"
    print(f"\n3. 保存到文件: {output_file}...")
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(postman_collection, f, indent=2, ensure_ascii=False)
        print(f"   ✅ 保存成功")
    except Exception as e:
        print(f"   ❌ 保存失败: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("✅ Postman Collection 导出完成！")
    print("=" * 60)
    print("\n📖 使用说明：")
    print("1. 打开 Postman")
    print("2. 点击 Import 按钮")
    print(f"3. 选择文件: {output_file}")
    print("4. 在 Collection Variables 中设置 api_key 变量")
    print("\n💡 提示：")
    print(f"   - 集合名称: {postman_collection['info']['name']}")
    print(f"   - 默认地址: http://localhost:8001")
    print(f"   - API Key 变量: {{{{api_key}}}}")
    print("")

if __name__ == "__main__":
    main()
