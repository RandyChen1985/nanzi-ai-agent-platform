from typing import Optional
from app.services.ai.tools.tool_compat import tool

@tool
def update_dashboard_context(
    room_name: Optional[str] = None,
    metric_name: Optional[str] = None,
    time_range: Optional[str] = None
):
    """
    Call this tool to update the user's dashboard context/state when the conversation involves specific entities.
    This helps the UI display relevant information alongside the chat.
    
    Args:
        room_name: The name of the room discussed (e.g. "Room 302", "302").
        metric_name: The metric being discussed (e.g. "PUE", "Power", "Temperature").
        time_range: The time range discussed (e.g. "24h", "7d", "today").
    """
    # This function body is just a placeholder. 
    # The AgentService will intercept this tool call and emit an event.
    return "Context updated."
