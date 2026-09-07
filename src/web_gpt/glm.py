"""Bounded coding delegation through the officially supported Claude Code CLI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .runtime import data_dir

ENDPOINTS = {
    "bigmodel": "https://open.bigmodel.cn/api/anthropic",
    "zai": "https://api.z.ai/api/anthropic",
}


def config_path() -> Path:
    return data_dir() / "glm.json"


def load_config() -> dict:
    return json.loads(config_path().read_text()) if config_path().exists() else {}


def credential(config: dict) -> str:
    # Read only the explicitly configured source. Never log or copy its contents.
    if config.get("zcode_config"):
        source = json.loads(Path(config["zcode_config"]).read_text())
        key = source["provider"][config["zcode_provider"]]["options"]["apiKey"]
    else:
        key = os.environ.get("GLM_CODING_API_KEY", "")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("没有可用 Key。设置 GLM_CODING_API_KEY，或配置已有 ZCode Key 的来源。不要把 Key 发到聊天中。")
    return key.strip()


def configure(provider: str, model: str, zcode_config: str | None = None,
              zcode_provider: str | None = None) -> dict:
    if provider not in ENDPOINTS or not re.fullmatch(r"glm-[a-zA-Z0-9.\[\]-]+", model):
        raise ValueError("请选择 bigmodel/zai 和明确的 glm-* 模型名称。")
    config = {"provider": provider, "model": model}
    if zcode_config:
        config.update(zcode_config=str(Path(zcode_config).expanduser().resolve()),
                      zcode_provider=zcode_provider or f"builtin:{provider}-coding-plan")
        credential(config)
    # This file contains only provider/model and optional credential source path.
    destination = config_path()
    with open(destination, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(config, stream)
    return {"status": "configured", "provider": provider, "model": model,
            "endpoint": ENDPOINTS[provider], "credential_source": "zcode" if zcode_config else "environment"}


def child_environment(config: dict, key: str, timeout: int) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ANTHROPIC_", "CLAUDE_", "CLAUDECODE"))}
    root = data_dir() / "glm-claude"
    root.mkdir(mode=0o700, exist_ok=True)
    env.update({
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_BASE_URL": ENDPOINTS[config["provider"]],
        "ANTHROPIC_DEFAULT_SONNET_MODEL": config["model"],
        "ANTHROPIC_DEFAULT_OPUS_MODEL": config["model"],
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": config["model"],
        "CLAUDE_CONFIG_DIR": str(root),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "API_TIMEOUT_MS": str(timeout * 1000),
    })
    return env


def ask(prompt: str, timeout: int = 600, model: str | None = None, effort: str = "high") -> dict:
    if not prompt.strip() or not 1 <= timeout <= 1800 or effort not in {"low", "medium", "high"}:
        return {"status": "invalid_input", "message": "需要非空 prompt；timeout 为 1–1800 秒，effort 为 low/medium/high。"}
    try:
        config = load_config()
        if config.get("provider") not in ENDPOINTS:
            return {"status": "not_configured", "message": "先运行 glm-code configure，明确订阅所属服务。"}
        if model:
            config["model"] = model
        if not re.fullmatch(r"glm-[a-zA-Z0-9.\[\]-]+", config.get("model", "")):
            return {"status": "invalid_input", "message": "必须指定 glm-* 模型，不使用 Claude 别名或自动回退。"}
        key = credential(config)
        executable = shutil.which("claude")
        if not executable:
            return {"status": "cli_unavailable", "message": "需要安装 Claude Code CLI；实际调用模型为 GLM。"}
        command = [executable, "-p", "--output-format", "json", "--model", config["model"],
                   "--effort", effort, "--tools", "", "--no-session-persistence", "--no-chrome",
                   "--disable-slash-commands", "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        # No tools, project files, global settings or MCP are loaded in the child.
        with tempfile.TemporaryDirectory(prefix="glm-code-") as cwd:
            result = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                    cwd=cwd, env=child_environment(config, key, timeout), timeout=timeout)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "cli_error", "message": "Claude Code 未返回 JSON；请检查 CLI 版本和配置。原始日志未回显，以免泄露凭据。"}
        if not isinstance(payload, dict):
            return {"status": "cli_error", "message": "CLI 返回格式无法识别，未确认完成。"}
        answer = str(payload.get("result", "")).replace(key, "[REDACTED]")
        if result.returncode or payload.get("is_error"):
            return {"status": "provider_error", "message": answer[:1000], "provider": config["provider"],
                    "model": config["model"]}
        if not answer.strip():
            return {"status": "cli_error", "message": "CLI 没有返回答案，未确认完成。"}
        return {"status": "completed", "answer": answer, "provider": config["provider"],
                "model": config["model"], "requested_effort": effort,
                "duration_ms": payload.get("duration_ms"), "usage": payload.get("usage")}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "等待到时，本次 CLI 已终止；远端可能已消耗额度，不会自动重发。"}
    except (OSError, ValueError, KeyError):
        return {"status": "configuration_error", "message": "无法读取 GLM 配置或 Key。检查本机配置来源；不要在聊天中发送 Key。"}


def main():
    parser = argparse.ArgumentParser(description="Codex → Claude Code CLI → GLM Coding Plan（文本代码任务）")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("configure", help="只保存服务、模型和 Key 来源路径，不复制 Key")
    setup.add_argument("--provider", choices=ENDPOINTS, required=True)
    setup.add_argument("--model", default="glm-5.2")
    setup.add_argument("--zcode-config")
    setup.add_argument("--zcode-provider", help="读取 ZCode 配置中的具体 provider 键")
    sub.add_parser("doctor", help="检查本机 CLI 和 Key 是否就绪，不发送请求")
    send = sub.add_parser("ask", help="委派一次独立代码任务并返回 JSON；不读取工作区或修改文件")
    source = send.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt")
    source.add_argument("--prompt-file", type=Path)
    send.add_argument("--model")
    send.add_argument("--effort", choices=["low", "medium", "high"], default="high")
    send.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    try:
        if args.command == "configure":
            result = configure(args.provider, args.model, args.zcode_config, args.zcode_provider)
        elif args.command == "doctor":
            config = load_config()
            credential(config)
            ready = config.get("provider") in ENDPOINTS and bool(shutil.which("claude"))
            result = {"status": "ready" if ready else "not_configured", "provider": config.get("provider"),
                      "model": config.get("model"), "key_present": True, "network_verified": False}
        else:
            prompt = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
            result = ask(prompt, args.timeout, args.model, args.effort)
    except (OSError, ValueError, KeyError):
        result = {"status": "configuration_error", "message": "配置或输入文件不可用。Key 只应保留在本机。"}
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] not in {"configured", "ready", "completed"}:
        sys.exit(1)


if __name__ == "__main__":
    main()
