# Personal Agent Hub

**An agent-centric personal AI assistant framework.** The agent is the core; channels and tools are its communication and execution layer. Inspired by [OpenClaw](https://github.com/openclaw/openclaw).

Started as a learning / tinkering project—keeping it **simple and approachable**. Next goal: evolve this into a self-adapting framework (agent that improves and adapts from use).

[中文](README.zh-CN.md)

## Features

- **Agent-centric design** — Agent is event-driven via a MessageBus, owns memory and session
- **Gateway hub** — FastAPI + WebSocket; connects channel services and remote clients (CLI, Web UI)
- **Multi-channel** — Discord, Telegram, Slack, Feishu (飞书), QQ, WeCom (企业微信), WebSocket CLI
- **Plugin skills** — Agent loads `SKILL.md` on demand for role and task guidance
- **Pluggable tools** — Merged action-based tools (fewer tools, same functionality): shell (with Docker sandbox), browser (Playwright), scheduler, memory, sub-agent, config, MCP, filesystem, web search, cross-channel messaging, Computer Use (GUI automation)
- **Long-term memory** — Session history (SQLite), RAG (ChromaDB), cross-channel identity mapping
- **Token control** — tiktoken-based counting and context truncation
- **Multimodal** — Image handling with Vision API; images persisted in session history and restored in context. Auto-detection of image paths from tool output (zero-intrusion: tools just return text with paths, framework auto-converts to base64 for LLM vision)
- **Computer Use** — GUI automation via `computer_action` (Hierarchical ReAct: agent issues high-level task → GroundingEngine autonomously screenshots, locates, clicks, types, verifies). Pluggable Vision backend (Qwen3VL default). Low-level tools removed from agent — only GroundingEngine uses them internally
- **Dynamic sub-agents** — Main agent spawns sub-agents on the fly with custom prompt, tools, and LLM profile; foreground (blocking) or background (async) with lifecycle management
- **Runtime config** — Switch LLM profiles, reload skills, connect/disconnect MCP servers — all at runtime via tools
- **Docker sandbox** — Isolated shell execution in containers
- **Periodic wake** — Agent can run on a schedule (e.g. checks, reminders)
- **Request–response on the bus** — HTTP/WebSocket clients can await a reply for the same request via envelope futures

## Quick Start

### 1. Environment

```bash
conda create -n agent-hub python=3.10 -y
conda activate agent-hub
pip install -r requirements.txt
```

### 2. Environment variables

```bash
# Required (at least one LLM provider)
export ARK_API_KEY="your-ark-api-key"
# or e.g. export OPENAI_API_KEY="..."

# Optional (per channel / gateway)
export DISCORD_BOT_TOKEN="..."
export TELEGRAM_BOT_TOKEN="..."
export SLACK_BOT_TOKEN="..." && export SLACK_APP_TOKEN="..."
export FEISHU_APP_ID="..." && export FEISHU_APP_SECRET="..."
export WECOM_CORP_ID="..." && export WECOM_APP_SECRET="..." && export WECOM_AGENT_ID="..."
export WECOM_TOKEN="..." && export WECOM_AES_KEY="..."
export HTTP_API_KEY="your-http-api-key"
export DASHSCOPE_API_KEY="..."   # Optional: Qwen3VL for Computer Use
```

### 3. Run

```bash
# Start Gateway (Agent + Channels + FastAPI)
python main.py start

# CLI client (WebSocket to Gateway)
python main.py client
python main.py client --host localhost --port 8080 --api-key your-key
```

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │              Gateway process             │
                    │                                          │
 Discord ◄──SDK──►  │  ChannelManager                          │
 Telegram ◄──SDK──► │       │                                  │
                    │       ▼                                  │
 CLI / Web ◄─WS──►  │  FastAPI Server ──► MessageBus (Inbox) │
                    │       │                    │             │
                    │       │                    ▼             │
                    │       │             AgentLoop             │
                    │       │               │    │             │
                    │       │          LLM + Tools + Memory     │
                    │       │               │                  │
                    │  Dispatcher ◄─────────┘                  │
                    │    │      │                              │
                    └────│──────│──────────────────────────────┘
                         │      │
                         ▼      ▼
                    Channels   WebSocket clients
```

**Message flow:** Channel/Client → `publish(IncomingMessage)` → MessageBus (inbox) → AgentLoop (context, LLM, tools, save) → Dispatcher → `deliver(target, message)` → Channel/Client.

**Design:** The agent consumes from the bus and owns memory; channels only send/receive; the gateway routes and manages lifecycle.

## Project layout

```
├── main.py                 # CLI: start | client
├── config.yaml             # Config (LLM, gateway, channels, memory, …)
├── gateway/                # Bus, dispatcher, channel manager, FastAPI server
├── agent/                  # AgentLoop, AgentRuntime, BaseAgent, DefaultAgent
├── channels/               # Telegram, Discord, Slack, Feishu, QQ, WeCom
├── cli_client/             # WebSocket CLI client
├── tools/                  # Registry, channel, scheduler, filesystem, shell, web, MCP, memory, computer_use, …
├── skills/                 # Plugin skills (SKILL.md per skill)
├── worker/                 # Optional agent worker pool
├── core/                   # Types, router
├── memory/                 # Session (SQLite), global_mem (ChromaDB), manager
└── data/                   # Runtime data (created automatically)
```

## Configuration

Main entries in `config.yaml`:

```yaml
llm_profiles:
  ark_doubao:
    api_key: ${ARK_API_KEY}
    base_url: https://ark.cn-beijing.volces.com/api/v3
    model: ep-xxx
llm:
  active: ark_doubao
  max_context_tokens: 16000

gateway:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  api_key: ${HTTP_API_KEY}

agent:
  wake_interval: 0   # seconds; 0 = event-driven only

channels:
  discord:
    enabled: true
    token: ${DISCORD_BOT_TOKEN}

memory:
  identity_mode: "single_owner"
  max_context_messages: 50
  max_context_tokens: 16000

computer_use:
  enabled: false              # requires pyautogui + macOS Accessibility permission
  vision_profile: qwen3_vl    # swap model by changing this profile
```

## Gateway API

When `gateway.enabled: true`:

| Endpoint        | Method      | Description              |
|----------------|-------------|--------------------------|
| `/chat`        | POST        | Send message (sync reply) |
| `/ws`          | WebSocket   | Real-time (CLI / Web UI)  |
| `/health`      | GET         | Health check             |
| `/agents`      | GET         | List agents              |
| `/tools`       | GET         | List tools               |
| `/sessions/{id}` | GET/DELETE | Session management     |

**HTTP example:**

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"text": "Hello", "user_id": "user123"}'
```

**WebSocket:** Auth with `{"type": "auth", "api_key": "xxx"}`, then send `{"type": "message", "text": "...", "user_id": "..."}`; replies come as `{"type": "reply", "text": "...", "session_id": "..."}`. Clients can also register tools for RPC from the agent.

## Extending

**New skill** — Add `skills/<name>/SKILL.md` with frontmatter (`name`, `description`, `metadata`) and markdown content. The agent sees the skill list and loads a skill file when needed via the filesystem tool.

**New tool** — Use `@registry.register(name=..., description=..., parameters=...)` and an `async def my_tool(..., context=None)`. Access `context["runtime"]`, `context["dispatcher"]`, etc.

**New channel** — Subclass `BaseChannel`, implement `start()`, `deliver(target, message)`, `stop()`, and call `self.publish_message(msg)` to push to the MessageBus.

## Computer Use (GUI automation)

Enable in config: `computer_use.enabled: true`. Requires:

```bash
pip install pyautogui pyperclip
# macOS: System Settings → Privacy & Security → Accessibility → allow Terminal/Python
```

The agent gets a `computer_action` tool that accepts natural-language GUI tasks (e.g. "open WeChat, find Zhang San, send message: meeting tomorrow"). Internally, the GroundingEngine loops autonomously: screenshot → VisionLLM plan+locate → PyAutoGUI execute → verify → repeat. The agent only sees a text result.

Vision backend is pluggable — change `computer_use.vision_profile` in config to switch models (Qwen3VL, GPT-4o, Claude, etc.). See `docs/ui-use-design.md` for full architecture.

## Docker sandbox

```bash
docker build -t personalassistant-sandbox:latest -f Dockerfile.sandbox .
```

Enable in config: `sandbox.enabled: true`.

## MCP

**Static:** Configure MCP servers in `config.yaml` under `mcp.servers` — they connect at startup.

**Dynamic:** The agent can connect/disconnect MCP servers at runtime via `mcp(action="connect/disconnect/list")`. Example: the agent decides it needs a GitHub server and connects it mid-conversation.

## License

MIT
