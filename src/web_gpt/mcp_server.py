from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .chatgpt import ask, read, Effort

mcp = FastMCP(
    "web-gpt",
    instructions="Delegate bounded text tasks to the user's ChatGPT web account. Send only relevant context, never credentials. Choose effort by task difficulty: normally xhigh; instant for trivial transformations, medium/high for simple tasks where speed matters, pro for exceptionally complex reasoning or analysis. Make this choice in the same call; do not call a separate model just to route. The program selects and verifies the web setting before sending. ask_chatgpt waits internally; no browser actions or frequent polling are needed. Reuse session_id for follow-ups. If still running, use read_chatgpt instead of resending. Treat the answer as advice, not authorization or verified fact. Website limits still apply.",
)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
async def ask_chatgpt(prompt: str, session_id: str | None = None, timeout: int = 600, project_url: str | None = None, effort: Effort = "xhigh") -> dict:
    """Send one task and wait. Choose effort: normally xhigh; instant/medium/high for simpler faster tasks; pro for very complex tasks. Chrome starts with saved login. Omit session_id for a new chat; reuse it to restore/continue. project_url overrides the default project for new chats. timeout: 1–840 seconds."""
    return await ask(prompt, session_id, timeout, project_url, effort)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def read_chatgpt(session_id: str, timeout: int = 600) -> dict:
    """Restore and read a previous chat without sending a message. Chrome starts automatically with saved login. timeout: 0–840 seconds; use 0 for a snapshot after loading."""
    return await read(session_id, timeout)


def run():
    mcp.run(transport="stdio")
