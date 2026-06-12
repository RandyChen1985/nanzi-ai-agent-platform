import asyncio
import sys
import os
import aiomysql

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings


def load_prompt_from_file():
    """Reads the prompt content from the associated markdown file."""
    prompt_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "architech", "prompts", "system_agents", "chatbi", "V8_chatbi_runner_aligned.md"
    )
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"❌ Error: Prompt file not found at {prompt_path}")
        sys.exit(1)

NEW_PROMPT = load_prompt_from_file()


async def update_prompt():
    print("🚀 Updating ChatBI System Prompt...")
    
    pool = await aiomysql.create_pool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DB,
        autocommit=True
    )

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 1. Find ChatBI Agent ID
                await cursor.execute("SELECT id FROM ai_agents WHERE name = 'chat-bi'")
                agent = await cursor.fetchone()
                if not agent:
                    print("❌ Error: Agent 'chat-bi' not found.")
                    return
                
                agent_id = agent[0]
                
                # 2. Update the PUBLISHED version prompt
                sql = "UPDATE ai_agent_versions SET system_prompt = %s WHERE agent_id = %s AND status = 'PUBLISHED'"
                await cursor.execute(sql, (NEW_PROMPT, agent_id))
                
                print(f"✅ Successfully updated prompt for agent_id: {agent_id}")
                
    except Exception as e:
        print(f"❌ Failed: {e}")
    finally:
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(update_prompt())
