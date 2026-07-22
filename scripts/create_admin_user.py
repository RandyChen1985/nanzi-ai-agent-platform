"""
单独创建管理员用户的脚本
功能：
1. 连接数据库
2. 创建管理员账号（使用新的加密机制）
"""
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

from sqlalchemy import select
from app.core import database
from app.core.orm import AsyncSessionLocal
from app.services.auth_service import AuthService
from app.models.user import User


async def create_admin_user():
    """创建管理员账号（使用新的加密机制）"""
    print("=" * 60)
    print("创建管理员账号")
    print("=" * 60)

    try:
        await database.init_db()

        # 检查是否已存在管理员（ORM 查询，替代旧的 raw cursor 写法）
        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(select(User).where(User.user_name == "admin"))
            ).scalar_one_or_none()
            if existing is not None:
                print("⚠️  管理员账号已存在，跳过创建")
                print()
                print("提示：如需重置，请先手动删除：")
                print("  DELETE FROM ai_agent_users WHERE user_name = 'admin';")
                return

        # 创建管理员（自动使用新的加密机制）
        print("正在创建管理员账号...")
        api_key = await AuthService.generate_api_key(
            user_name="admin",
            role="admin",
            remark="系统管理员"
        )

        print()
        print("✅ 管理员账号创建成功！")
        print()
        print("=" * 60)
        print("🔑 管理员 API Key（请立即保存）")
        print("=" * 60)
        print()
        print(f"   用户名: admin")
        print(f"   API Key: {api_key}")
        print()
        print("⚠️  重要提示：")
        print("   - 此 API Key 仅显示一次，请立即保存")
        print("   - 使用此 Key 登录前端或访问管理接口")
        print()

    except Exception as e:
        print(f"❌ 创建管理员失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await database.close_db()


if __name__ == "__main__":
    asyncio.run(create_admin_user())
