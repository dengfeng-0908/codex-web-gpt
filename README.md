# Codex Web GPT

通过 **CLI 或 MCP** 向 ChatGPT 网页发送任务及明确指定的图片／文件、等待答案并继续追问。固定程序负责浏览器收发，Codex 只提交任务并读取结果，不需要逐步操作页面或查看截图。另提供 `glm-code` CLI，通过 Claude Code 将独立代码任务交给 GLM Coding Plan。

这是一个非官方的实验项目。当前支持 **macOS、已安装的 Google Chrome，以及带“即时／中／高／极高／Pro”五档菜单的中文 ChatGPT 网页**。其他系统、语言或网页布局尚未验证。

## 功能

- 调用时自动启动专用 Chrome，使用独立且持久的浏览器配置目录。
- 首次手动登录后复用登录态；关闭标签页或退出浏览器后，按保存的会话网址恢复。
- 新建对话、同一对话追问、超时后继续读取，不自动重复发送结果不明的消息。
- 支持默认 ChatGPT 项目，也可为新对话单独指定项目。
- 发送前选择并核对推理档位；默认 `xhigh`，调用方可按任务难度降低或选择 `pro`。
- 提供 CLI 和两个 MCP 工具，等待与页面轮询在程序内部完成。
- 上传图片和文件，等待每个附件完成后才发送；保留原有附件草稿。
- 可选 GLM CLI 委派，不增加 MCP 工具；GLM 只接收指定文本并返回建议。

## 安装与首次登录

需要 macOS、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Google Chrome。项目使用 Python 3.12，依赖由 `uv.lock` 锁定。

```sh
git clone https://github.com/dengfeng-0908/codex-web-gpt.git
cd codex-web-gpt
uv sync --locked
.venv/bin/web-gpt browser
```

在专用 Chrome 中登录自己的 ChatGPT 账号，然后检查并发送一个简单问题：

```sh
.venv/bin/web-gpt doctor
.venv/bin/web-gpt ask --effort instant --prompt '请只回复：连接成功'
```

后续可直接运行 `ask` 或 `read`。浏览器未运行时会自动启动，正常重启不要求重复登录；如果网站让登录态失效，仍需本人重新登录。此项目使用网页会话，不需要 OpenAI API key。

## CLI

```sh
# 默认使用极高档位，新建对话。
.venv/bin/web-gpt ask --prompt '请分析这个问题：……'

# 使用返回的 session_id 继续原对话。
.venv/bin/web-gpt ask --session '<session_id>' --prompt '继续解释第二点'

# 等待未完成的回复；不发送新消息。
.venv/bin/web-gpt read '<session_id>' --timeout 600

# 大段上下文通过明确指定的 UTF-8 文件传入。
.venv/bin/web-gpt ask --effort pro --prompt-file /absolute/path/task.txt

# 真正上传图片或文件；--attach 可以重复使用。
.venv/bin/web-gpt ask --prompt '结合文档分析这张图' --attach /absolute/path/chart.png --attach /absolute/path/report.pdf

# 将项目首页的完整 URL 设为默认项目。
.venv/bin/web-gpt project 'https://chatgpt.com/g/g-p-.../project'

# 为此次新对话选择其他项目。
.venv/bin/web-gpt ask --project 'https://chatgpt.com/g/g-p-.../project' --prompt '任务内容'

# 查看或取消默认项目。
.venv/bin/web-gpt project
.venv/bin/web-gpt project --clear
```

项目 URL 示例中的 `...` 需要替换为网页实际地址。更改默认项目不会移动已有对话；追问始终使用原会话。

返回 JSON 包含 `status`、`session_id`、`url`、`effort` 和 `effort_label`；完成时包含 `answer`。只有 `completed` 表示已观察到回复完成，`running` 表示等待到时，应使用同一编号继续 `read`。回复等待参数最多为 840 秒；网页启动和加载另有等待时间。

`--prompt-file` 将 UTF-8 文本读入消息；`--attach` 才执行附件上传，只上传明确指定的本地文件，不扫描目录。支持的类型、大小和数量由 ChatGPT 网页决定，本项目实测 PNG 和 TXT；PDF 等其他类型尚未实测。网页可能给重名文件加 `(1)`，工具会识别这种改名。同轮附件应使用不同文件名。

上传最多等待 120 秒（不超过本次 `timeout`）。`upload_failed` 或 `upload_unconfirmed` 表示没有发送消息，已选附件可能保留在网页，请检查返回的 `url` 后处理；不自动重试或静默丢弃附件。遇到未发送文本或附件草稿会停止，不覆盖草稿。

## 推理档位

| `effort` | 网页档位 | 建议用途 |
| --- | --- | --- |
| `instant` | 即时 | 极简单的转换、格式整理，速度优先 |
| `medium` | 中 | 简单且边界清楚的问题 |
| `high` | 高 | 较简单但需要一定推理的问题 |
| `xhigh` | 极高 | 默认；通常的分析、设计和代码审查 |
| `pro` | Pro | 非常复杂的推理或综合分析 |

由调用方 Codex 理解任务难度，在同一次请求中选择 `effort`，不另调用模型做路由。人工直接运行 CLI 时，省略参数固定使用 `xhigh`。

程序操作网页可见的档位滑块，核对实际标签后才发送。所选档位不可用或布局不匹配时返回 `effort_unavailable`，不静默降档。底层模型版本和可用额度由网页账号决定。

## MCP（按需启用）

CLI 可以独立使用，不需要配置 MCP。如果希望在 Codex 中直接看到工具，在可信项目的 `.codex/config.toml` 中添加 [配置示例](examples/codex.toml)，将 `command` 改为本机的绝对路径；已有配置时只合并对应服务段。

```toml
[mcp_servers.web-gpt]
command = "/absolute/path/to/codex-web-gpt/.venv/bin/web-gpt"
args = ["mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 1140
enabled_tools = ["ask_chatgpt", "read_chatgpt"]
```

| 工具 | 用途 |
| --- | --- |
| `ask_chatgpt(prompt, session_id?, timeout=600, project_url?, effort="xhigh", attachments?)` | 选择档位、上传指定文件并发送；提供编号则恢复并追问 |
| `read_chatgpt(session_id, timeout=600)` | 恢复并读取已有任务，不发送新消息；`timeout=0` 在页面加载后读取一次状态 |

工具说明包含按难度选档、精简任务上下文和超时续读的规则。Skill 可用于包装 CLI 的调用方法，本仓库目前未提供单独的 Skill 安装包。

升级后需让 Codex 重新启动该 MCP 服务／在新任务中重新加载工具，才能看到新增的 `attachments` 参数；旧工具描述不会自动更新。CLI 可立即使用。

## GLM Coding Plan（可选 CLI）

调用方向为 **Codex → `glm-code` → Claude Code CLI → GLM**。Claude Code 是智谱官方支持的 Coding Plan 工具，实际请求使用明确的 `glm-*` 模型，不需要 Claude 订阅。需先安装 `claude` 命令。本桥接不会修改全局 Claude Code 配置，不依赖 ZCode 窗口保持打开，也不需要 VPS。[智谱接入文档](https://docs.bigmodel.cn/cn/guide/develop/claude)、[Claude Code CLI](https://code.claude.com/docs/en/cli-reference)。

先明确订阅属于国内 `bigmodel` 还是海外 `zai`，并使用对应的 Coding Plan Key。可以通过本机环境变量 `GLM_CODING_API_KEY` 提供 Key，再保存不含凭据的配置：

```sh
.venv/bin/glm-code configure --provider bigmodel --model glm-5.2
.venv/bin/glm-code doctor
.venv/bin/glm-code ask --prompt 'Review: def last(xs): return xs[len(xs)]'
.venv/bin/glm-code ask --prompt-file /absolute/path/code-review-task.txt --timeout 600
```

已有 ZCode 配置时，也可明确指定读取哪个 provider 的 Key；运行时只读取，不复制 Key，不修改 ZCode 配置。示例 provider 键须与自己的配置匹配：

```sh
.venv/bin/glm-code configure --provider bigmodel --model glm-5.2 \
  --zcode-config "$HOME/.zcode/v2/config.json" \
  --zcode-provider 'builtin:bigmodel-coding-plan'
```

`--provider` 决定实际请求地址；`--zcode-provider` 只决定 Key 来源。两者必须对应同一订阅服务，不能仅凭 Key 存放位置猜测。国内地址固定为 `https://open.bigmodel.cn/api/anthropic`，海外固定为 `https://api.z.ai/api/anthropic`，失败不会切换到其他计费接口。配置保存到本机应用数据目录的 `glm.json`，其中只有服务、模型和可选来源路径。

GLM 首版用于独立代码审查、解释、算法分析和生成建议；每次无历史，必要代码通过提示词提供。子进程没有文件、Shell、MCP 工具或项目配置，不会自行修改工作区。原有 Claude Code 设置和认证通过独立配置目录隔离。默认 `--effort high`，可选 `low/medium/high`；这是向 CLI 请求的档位，实际映射由 GLM 服务决定，不等同于 ChatGPT 的五档。可用 `--model glm-…` 指定套餐支持的其他模型，不自动降级。

返回 JSON 的 `completed` 才表示拿到答案；`timeout` 会终止本次 CLI，远端可能已经消耗额度，不自动重发。`doctor` 只验证本机配置，不能证明账号认证或剩余额度。订阅限额及支持工具范围仍适用，不能据此视为通用按量 API 余额。[套餐说明](https://docs.bigmodel.cn/cn/coding-plan/overview)

## 本机数据与运行限制

- 专用浏览器配置、默认项目与会话映射位于 `~/Library/Application Support/codex-web-gpt/`，不在源码目录内。可以通过 `WEB_GPT_DATA_DIR` 明确指定其他本机数据目录。
- 会话映射保存网址、回复 ID 和状态，不保存完整提示词或答案；Chrome 自身仍会保存普通浏览器缓存、历史和站点数据。
- 不复制日常浏览器 Cookie，不读取 ChatGPT 私有 API，不自动绕过验证码或用量限制。请保管好专用浏览器目录，不将其提交到仓库。
- 调试连接仅使用本机 `127.0.0.1:19231`。同时只允许一个请求使用浏览器。
- 空闲时可以退出 Chrome；正在收发时应保持浏览器与网络可用。电脑睡眠会影响本机执行。
- 如果首次发送后尚未取得会话 URL 就关闭浏览器，工具可能无法恢复；不会自动重发原任务。
- 本工具不下载 GPT 权重。Chrome 自身可能下载浏览器组件或内置 AI 模型；当前启动器尚未禁用这类下载。[Chrome 模型管理说明](https://developer.chrome.com/docs/ai/understand-built-in-model-management)

该接口用于分配任务和减少模型逐步操作网页的开销，不增加订阅额度，也不保证固定的节省比例。网页账号自身的权限与限制仍适用。普通 Chat 与 Work 的用量规则应分别核对；官方说明 ChatGPT Work 和 Codex 共享用量。[官方说明](https://learn.chatgpt.com/zh-Hans/docs/pricing)

## 开发与验证

```sh
uv sync --locked
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

测试使用本机 Chrome 和独立临时上下文，通过本地页面夹具验证收发、恢复、项目绑定、五档选择及真实 MCP 协议，不调用真实 ChatGPT。无需运行 `playwright install`。

2026-09-06 已完成 17 项本地测试，并分别做过真实 CLI 收发、独立 MCP 追问、关闭标签页后的只读恢复、退出浏览器后的免重新登录恢复和五档切换验证。即时档做过真实收发；Pro 仅验证切换生效，未以长任务验证其推理效果。真实账号验证不代表所有账号或后续网页版本兼容。

2026-09-07：20 项浏览器／协议测试与 5 项 GLM 委派测试通过，源码包和 wheel 构建通过。真实 CLI 同轮上传 PNG + TXT，网页版正确返回文件标记和图片颜色（22.5 秒）；独立 MCP 进程在同一会话上传新 TXT 并返回新标记（13.8 秒）。GLM 国内接口通过 Claude Code 2.1.63、`glm-5.2` 完成最小代码修复问答（服务报告约 2.8 秒）。没有读取订阅后台账单，GLM 海外接口和其他模型尚未实测。

真实追问验证中曾出现答案正常、整个输入框未渲染的情况，接口返回 `editor_unavailable` 且未发送；确认测试会话无草稿、用户消息仍为 1 条后刷新，输入框恢复，随后 MCP 附件追问成功。原因尚未确定，程序没有增加自动刷新或重发；遇到同类状态先检查网页，不按登录失效处理。

网页选择器集中在 `src/web_gpt/chatgpt.py`。报告问题时请附上系统、Chrome 版本和脱敏状态，不附 Cookie、浏览器配置目录或完整私人对话。

## 参考与许可证

连接方式参考了 [mcp-web-llm](https://github.com/HGD-coder/mcp-web-llm) 的架构方向，本项目聚焦 ChatGPT 独立实现。调研资源还包括 [coding-tools-mcp](https://github.com/xyTom/coding-tools-mcp)、[mybolide/coding-tools-mcp](https://github.com/mybolide/coding-tools-mcp)、[webcodex](https://github.com/yyjeqhc/webcodex) 和 [ChatCodex](https://github.com/AeroidesLab/ChatCodex)。

依赖与接口参考：[Playwright CDP](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp)。

本项目使用 [MIT License](LICENSE)。
