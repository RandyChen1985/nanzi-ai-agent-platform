import asyncio
import sys
import os
from sqlalchemy import select, update

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.orm import AsyncSessionLocal
from app.models.agent import AIAgent, AIAgentVersion


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

    try:
        async with AsyncSessionLocal() as session:
            agent_id = (
                await session.execute(
                    select(AIAgent.id).where(AIAgent.name == "chat-bi")
                )
            ).scalar_one_or_none()
            if not agent_id:
                print("❌ Error: Agent 'chat-bi' not found.")
                return

            await session.execute(
                update(AIAgentVersion)
                .where(
                    AIAgentVersion.agent_id == agent_id,
                    AIAgentVersion.status == "PUBLISHED",
                )
                .values(system_prompt=NEW_PROMPT)
            )
            await session.commit()
            print(f"✅ Successfully updated prompt for agent_id: {agent_id}")
    except Exception as e:
        print(f"❌ Failed: {e}")

if __name__ == "__main__":
    asyncio.run(update_prompt())
