import asyncio
import sys
import os
import importlib
import pkgutil

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 全量加载模型，确保 SQLAlchemy mapper 完整配置
# （否则 User.roles relationship 解析 ai_agent_user_role_relations 会失败）
import app.models as _models_pkg
for _mod in pkgutil.iter_modules(_models_pkg.__path__):
    importlib.import_module(f"app.models.{_mod.name}")

from sqlalchemy import text
from app.core import database
from app.core.orm import engine
from app.services.auth_service import AuthService
from app.core.config import settings

async def reset_and_init():
    print("🚀 开始重置数据库用户...")

    await database.init_db()

    # 1. 清空数据（engine.begin() + text()，替代旧的 raw cursor 写法）
    print("🧹 清空 ai_agent_users 表...")
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE ai_agent_users"))

    # 2. 创建管理员 (admin)
    print("👤 创建管理员账号 (admin)...")
    admin_key = await AuthService.generate_api_key(
        user_name="admin",
        role="admin",
        remark="超级管理员"
    )

    # 3. 创建普通用户 (demo_user)
    print("👤 创建普通用户 (demo_user)...")

    # 直接生成 API Key
    user_key = await AuthService.generate_api_key(
        user_name="demo_user",
        role="user",
        remark="演示用户"
    )

    print("\n" + "="*50)
    print("✅ 重置完成！请使用以下 Key 登录：")
    print("="*50)
    print(f"👑 [Admin]     用户: admin")
    print(f"🔑 API Key:    {admin_key}")
    print("-" * 50)
    print(f"🧑 [User]      用户: demo_user")
    print(f"🔑 API Key:    {user_key}")
    print("="*50 + "\n")

    await database.close_db()

if __name__ == "__main__":
    try:
        asyncio.run(reset_and_init())
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        # 如果是密码错误，提示用户
        if "Access denied" in str(e):
            print("\n⚠️  提示：无法连接数据库。")
            print("   请检查 .env 文件中的 MYSQL_PASSWORD 是否正确。")
