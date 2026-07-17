import asyncio
import os
import aiomysql
from dotenv import load_dotenv

load_dotenv()

async def list_tables():
    host = os.getenv("MYSQL_HOST", "localhost")
    port = int(os.getenv("MYSQL_PORT", 3306))
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("MYSQL_DB", "nanzi_ai_agent_platform")

    try:
        pool = await aiomysql.create_pool(
            host=host, port=port,
            user=user, password=password,
            db=db
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW TABLES")
                tables = await cur.fetchall()
                print("Current Tables:")
                for t in tables:
                    print(f"- {t[0]}")
        pool.close()
        await pool.wait_closed()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(list_tables())
