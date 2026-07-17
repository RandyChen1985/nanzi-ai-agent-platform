import asyncio
import os
import json
import httpx
from app.services.ai.openclaw_client import OpenClawClient

async def test_real_openclaw_call():
    """
    真正的网络请求测试：直接请求生产环境 OpenClaw API。
    """
    print("🚀 [1/3] 初始化 OpenClawClient (真实生产环境)...")
    client = OpenClawClient()
    
    # 使用您提供的真实配置
    base_url = "https://yunshu-openclaw.yovole.net/"
    api_key = "7c203522923b5f9fa2fabdf274c4fc2a129b1037f58574a9"
    model_id = "openclaw:nanzi_bot"

    print(f"📡 目标地址: {base_url}")
    print(f"🤖 机器人 ID: {model_id}")

    # 构造请求参数
    query = "你好，请自我介绍并确认你现在是否收到了来自 'chenxiaolong' 的请求。如果收到了，请用中文简洁回复。"
    history = []
    
    config = {
        "model": model_id,
        "base_url": base_url,
        "api_key": api_key,
        "stream": False # 尝试关闭流式
    }

    print(f"\n🚀 [2/3] 发送真实非流式请求 (User: chenxiaolong, Stream: False)...")
    print("-" * 50)
    
    full_answer = ""
    try:
        async for chunk in client.chat_stream(
            query=query,
            history=history,
            config=config,
            conversation_id="test-real-prod-flow",
            user="chenxiaolong" # 强制使用您的英文名
        ):
            if chunk.get("type") == "answer":
                content = chunk.get("content", "")
                full_answer += content
                print(content, end="", flush=True)
            elif chunk.get("type") == "error":
                print(f"\n❌ API 报错: {chunk.get('content')}")

        print("\n" + "-" * 50)
        print(f"🚀 [3/3] 真实请求测试结束。")
        if full_answer:
            print(f"✅ [SUCCESS] 已成功收到 OpenClaw 真实返回！")
        else:
            print(f"⚠️ [WARNING] 请求已完成但没有收到内容。请检查 OpenClaw 服务状态。")

    except Exception as e:
        print(f"\n❌ 网络请求异常: {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_real_openclaw_call())
