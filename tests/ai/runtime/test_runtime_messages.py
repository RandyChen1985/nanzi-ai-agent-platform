from app.services.ai.runtime.agentscope.messages import (
    DEFAULT_LLM_USER_PROMPT,
    system_user_prompt_messages,
)


def test_system_user_prompt_messages_defaults_user_prompt():
    messages = system_user_prompt_messages("system instructions")
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[0].content[0].text == "system instructions"
    assert messages[1].role == "user"
    assert messages[1].content[0].text == DEFAULT_LLM_USER_PROMPT


def test_system_user_prompt_messages_custom_user_prompt():
    messages = system_user_prompt_messages("system instructions", user_prompt="custom user")
    assert messages[1].content[0].text == "custom user"
