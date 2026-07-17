import requests
import json
import time

"""
# 南孜·智能体平台 API 调用示例 (Python)
该脚本演示了如何使用 requests 库调用通用查询接口。
"""

# 配置信息
BASE_URL = "http://localhost:8001/api/v1"
API_KEY = "your_api_key_here"  # 替换为真实的 API Key

def execute_logical_query(resource, filters=None, sort_by=None, page=1, size=10):
    """执行通用逻辑查询"""
    url = f"{BASE_URL}/query/"
    headers = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "resource": resource,
        "filters": filters or [],
        "sort_by": sort_by,
        "page": page,
        "size": size
    }
    
    print(f"--- Sending Request to {resource} ---")
    response = requests.post(url, headers=headers, json=payload)
    
    if response.status_code == 200:
        result = response.json()
        data = result.get("data", {})
        print(f"Success! Total items: {data.get('total')}")
        return data.get("items", [])
    else:
        print(f"Error {response.status_code}: {response.text}")
        return None

def get_nanzi_rooms():
    """获取南孜机房列表示例"""
    url = f"{BASE_URL}/resources/rooms"
    headers = {"X-API-Key": API_KEY}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get("data", {}).get("items", [])
    return []

if __name__ == "__main__":
    # 示例 1: 使用通用查询获取高报警指标
    high_temp_metrics = execute_logical_query(
        resource="donghuan_real_metrics",
        filters=[["metric_value", ">", "35"]],
        sort_by="metric_time",
        size=5
    )
    
    if high_temp_metrics:
        for m in high_temp_metrics:
            print(f"Time: {m['metric_time']} | Value: {m['metric_value']}")

    # 示例 2: 获取机房列表
    rooms = get_nanzi_rooms()
    print(f"\nAvailable Rooms: {[r.get('room_name') for r in rooms]}")
