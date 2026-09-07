# Codex Web GPT

- Read README.md for setup, supported environments and current limitations.
- Use the project Python 3.12 environment: `uv sync --locked`, then `.venv/bin/python`.
- Keep browser profiles, credentials, local project settings and session metadata outside Git.
- Use the dedicated Chrome profile. Do not copy cookies, call private ChatGPT APIs, bypass challenges or retry an ambiguous send automatically.
- Prefer the existing CLI for local delegation. Normally choose `xhigh`; use lower effort for simple tasks and `pro` for exceptionally complex ones. Select effort in the same call.
- `web-gpt ask --attach PATH` / MCP `attachments` uploads explicit files and images. `--prompt-file` only embeds UTF-8 text. Leave unconfirmed uploads in the composer and report them; never automatically resend.
- `glm-code ask --prompt-file PATH` delegates a bounded coding task through Claude Code to GLM. It has no file/shell/MCP tools or persistent conversation; supply the necessary code as text. Provider and credential source must be explicitly configured locally. Do not change endpoints, use paid fallback APIs or publish credentials implicitly.
- Keep changes focused. Run `.venv/bin/python -m unittest discover -s tests -v` and `git diff --check`. Changes to browser interactions need a separate, explicitly scoped real-page smoke test.
- Tests use a local page fixture; do not present fixture results as proof of real-account compatibility.
- Do not add global MCP configuration, remote deployment or background services implicitly.
