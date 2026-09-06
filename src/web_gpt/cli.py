from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .chatgpt import ask, doctor, read, EFFORTS
from .runtime import launch_browser, default_project, set_default_project


def main():
    parser = argparse.ArgumentParser(description="Codex → ChatGPT 网页：MCP / CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("browser", help="打开独立 Chrome 登录窗口")
    sub.add_parser("doctor", help="检查连接与输入框，不读取聊天正文")
    sub.add_parser("mcp", help="启动 stdio MCP，供 Codex 桌面版或 CLI 调用")
    project = sub.add_parser("project", help="设置或查看默认 ChatGPT 项目")
    project.add_argument("url", nargs="?", help="项目首页完整 URL")
    project.add_argument("--clear", action="store_true", help="取消默认项目")
    send = sub.add_parser("ask", help="发送任务并等待；提供 session 则继续追问")
    source = send.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt")
    source.add_argument("--prompt-file", type=Path)
    send.add_argument("--session")
    send.add_argument("--project", help="此次新会话使用的 ChatGPT 项目首页 URL")
    send.add_argument("--effort", choices=EFFORTS, default="xhigh", help="推理档位；默认极高 xhigh，简单任务可降低，复杂任务可用 pro")
    send.add_argument("--timeout", type=int, default=600)
    receive = sub.add_parser("read", help="继续等待现有任务；绝不发送新消息")
    receive.add_argument("session_id")
    receive.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if args.command == "mcp":
        from .mcp_server import run
        run()
        return
    if args.command == "browser":
        result = launch_browser()
    elif args.command == "project":
        try:
            result = set_default_project(None if args.clear else args.url) if args.clear or args.url else {"status": "configured", "default_project_url": default_project()}
        except ValueError as error:
            result = {"status": "invalid_input", "message": str(error)}
    elif args.command == "doctor":
        result = asyncio.run(doctor())
    elif args.command == "ask":
        prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
        result = asyncio.run(ask(prompt, args.session, args.timeout, args.project, args.effort))
    else:
        result = asyncio.run(read(args.session_id, args.timeout))
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] not in {"completed", "ready", "launched", "already_running", "running", "configured"}:
        sys.exit(1)


if __name__ == "__main__":
    main()
