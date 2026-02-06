# Personal Agent Hub

Python version of OpenClawd

一个 Agent-Centric 的个人 AI 助手框架。Agent 是系统核心主体，Channel 和 Tool 是 Agent 的沟通与执行工具。

## 特性

- **Agent-Centric 架构**: Agent 通过 MessageBus 事件驱动，自主管理记忆和会话
- **Gateway 中心枢纽**: FastAPI + WebSocket，连接 Channel Services 和远程 Client
- **多渠道接入**: Telegram / Discord / WebSocket CLI Client
- **插件式 Skills**: Agent 按需加载 SKILL.md 获取专业指导
- **可插拔 Tools**: 定时提醒、文件操作、Shell 执行、网页搜索、MCP 协议、跨渠道消息发送
- **长期记忆**: Session 历史 (SQLite) + RAG 向量搜索 (ChromaDB) + 跨渠道身份统一
- **Token 管理**: tiktoken 精确计数，智能截断上下文
- **多模态支持**: 图片处理与 Vision API 集成
- **Docker 沙箱**: 容器隔离执行 Shell 命令
- **进程解耦**: Gateway/Agent 分离，Worker 进程池
- **Sub-Agent 系统**: 生成子 Agent 执行复杂任务
- **Memory Tools**: Agent 主动搜索和添加记忆

## 快速开始

### 1. 创建 Conda 环境

```bash
conda create -n agent-hub python=3.10 -y
conda activate agent-hub
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 必需
export ARK_API_KEY="your-ark-api-key"

# 可选（按需设置）
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
export DISCORD_BOT_TOKEN="your-discord-bot-token"
export HTTP_API_KEY="your-http-api-key"
```

### 3. 运行

```bash
# 启动 Gateway（包含 Agent + Channels + FastAPI）
python main.py start

# 单次对话测试
python main.py chat "你好"

# 启动 CLI Client（通过 WebSocket 连接 Gateway）
python main.py client
python main.py client --host localhost --port 8080 --api-key your-key
```

## 系统架构

```
                    ┌─────────────────────────────────────────┐
                    │           Gateway 进程                   │
                    │                                          │
 Discord ◄──SDK──► │  ChannelManager                          │
Telegram ◄──SDK──► │       │                                  │
                    │       ▼                                  │
 CLI Client ◄─WS─► │  FastAPI Server ──► MessageBus (Inbox)  │
 Web UI ◄──WS/H──► │       │                    │             │
                    │       │                    ▼             │
                    │       │             AgentLoop            │
                    │       │               │    │             │
                    │       │          LLM+Tools Memory        │
                    │       │               │                  │
                    │  Dispatcher ◄─────────┘                  │
                    │    │      │                              │
                    └────│──────│──────────────────────────────┘
                         │      │
                         ▼      ▼
                    Channels  WebSocket Clients
```

### 消息流

```
Channel/Client 收到消息
    │
    ▼ publish(IncomingMessage)
MessageBus (Inbox Queue)
    │
    ▼ consume()
AgentLoop
    ├── AgentRuntime.load_context() → 历史 + 记忆 (Token 截断)
    ├── BaseAgent.run() → LLM 处理 + Tool 调用
    │       └── 根据 Skill 清单按需加载 SKILL.md
    ├── AgentRuntime.save_message() → 保存对话
    └── Dispatcher.dispatch_reply() → 路由回复
    │
    ▼
Channel/Client 收到回复
```

### 核心设计理念

- **Agent 是主体**：Agent 从 MessageBus 取消息、自己管理记忆、通过 Tool 主动发消息
- **Channel 是服务**：Channel 是独立运行的通讯服务，不包含业务逻辑
- **Gateway 是枢纽**：Gateway 负责消息路由和组件生命周期，不参与 Agent 决策

## 项目结构

```
personal_agent_hub/
├── main.py                    # CLI 入口（start/chat/client）
├── config.yaml                # 配置文件
├── gateway/                   # Gateway 中心枢纽
│   ├── app.py                 # Gateway 主类（替代旧 Engine）
│   ├── bus.py                 # MessageBus（异步消息队列）
│   ├── dispatcher.py          # 出站消息路由
│   ├── channel_manager.py     # Channel 生命周期管理
│   └── server.py              # FastAPI + WebSocket 服务
├── agent/                     # Agent 运行时
│   ├── loop.py                # AgentLoop（事件驱动主循环）
│   ├── runtime.py             # AgentRuntime（Memory + 身份解析）
│   ├── base.py                # BaseAgent（LLM + Tool 执行）
│   └── default.py             # DefaultAgent（通用助手）
├── channels/                  # Channel Services
│   ├── base.py                # Channel 基类（MessageBus 集成）
│   ├── cli.py                 # CLI Channel（本地调试用）
│   ├── telegram.py            # Telegram Bot（自动重连）
│   └── discord.py             # Discord Bot（自动重连）
├── cli_client/                # 远程 CLI 客户端
│   └── client.py              # WebSocket CLI（类 Claude Code）
├── tools/                     # 可插拔工具
│   ├── registry.py            # Tool 注册（支持 MCP）
│   ├── channel.py             # 跨渠道消息发送（send_message）
│   ├── scheduler.py           # 定时提醒
│   ├── filesystem.py          # 文件操作
│   ├── shell.py               # Shell 执行
│   ├── web.py                 # 网页搜索/抓取
│   ├── image.py               # 图片处理
│   ├── sandbox.py             # Docker 沙箱
│   ├── mcp_client.py          # MCP 协议客户端
│   ├── memory.py              # 记忆工具
│   └── subagent.py            # Sub-Agent 系统
├── skills/                    # Skills 插件目录
│   ├── loader.py              # Skill 加载器
│   ├── study_coach/SKILL.md
│   ├── coding_assistant/SKILL.md
│   └── project_manager/SKILL.md
├── worker/                    # 进程解耦
│   ├── agent_worker.py        # Agent Worker 进程
│   ├── agent_client.py        # Gateway 端客户端
│   ├── pool.py                # Worker 进程池
│   └── protocol.py            # 通信协议
├── core/                      # 共享模块
│   ├── types.py               # 类型定义（Message, Envelope 等）
│   └── router.py              # 消息路由
├── memory/                    # 记忆系统
│   ├── session.py             # 对话历史 (SQLite)
│   ├── global_mem.py          # 长期记忆 (ChromaDB)
│   └── manager.py             # Memory 管理
├── utils/
│   └── token_counter.py       # Token 计数器
├── Dockerfile.sandbox         # 沙箱镜像
└── data/                      # 数据目录（自动创建）
```

## 配置说明

配置文件 `config.yaml` 主要配置项：

```yaml
# LLM 多 Provider 支持
llm_profiles:
  ark_doubao:
    api_key: ${ARK_API_KEY}
    base_url: https://ark.cn-beijing.volces.com/api/v3
    model: ep-xxx
    extra_params:
      reasoning_effort: medium
  deepseek_reasoner:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com
    model: deepseek-reasoner
    features:
      preserve_reasoning_content: true

llm:
  active: ark_doubao  # 切换 Provider 只需改这里
  max_context_tokens: 16000

# Gateway 配置
gateway:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  api_key: ${HTTP_API_KEY}

# Agent 配置
agent:
  wake_interval: 0  # 周期性唤醒（秒），0 = 仅事件驱动

# Channel 配置
channels:
  telegram:
    enabled: false
    token: ${TELEGRAM_BOT_TOKEN}
  discord:
    enabled: true
    token: ${DISCORD_BOT_TOKEN}

# 记忆系统
memory:
  identity_mode: "single_owner"
  max_context_messages: 50
  max_context_tokens: 16000
```

## Gateway API

启用 `gateway.enabled: true` 后可用：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 发送消息（同步回复） |
| `/ws` | WebSocket | 实时通信（CLI Client / Web UI） |
| `/health` | GET | 健康检查 |
| `/agents` | GET | 列出 Agents |
| `/tools` | GET | 列出 Tools |
| `/sessions/{id}` | GET/DELETE | 会话管理 |

### HTTP 接口

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"text": "你好", "user_id": "user123"}'
```

### WebSocket 协议

```json
// 1. 认证
→ {"type": "auth", "api_key": "xxx"}
← {"type": "auth_ok", "connection_id": "..."}

// 2. 发送消息
→ {"type": "message", "text": "你好", "user_id": "cli_user"}
← {"type": "reply", "text": "...", "session_id": "..."}

// 3. 服务端推送
← {"type": "push", "text": "..."}
```

## 扩展开发

### 添加新 Skill（插件式）

在 `skills/` 目录下创建 `{skill_name}/SKILL.md`：

```markdown
---
name: my_skill
description: 技能描述
metadata:
  emoji: "🎯"
---

# 角色定义

你是一个...
```

Agent 在 system prompt 中看到可用 skill 清单，需要时通过 `read_file("skills/xxx/SKILL.md")` 按需加载。

### 添加新 Tool

```python
from tools.registry import registry

@registry.register(
    name="my_tool",
    description="工具描述",
    parameters={...}
)
async def my_tool(arg1: str, context=None) -> str:
    runtime = context["runtime"]  # AgentRuntime 引用
    dispatcher = context["dispatcher"]  # Dispatcher 引用
    return "结果"
```

### 添加新 Channel

继承 `BaseChannel`，实现 `start()`, `send()`, `stop()` 方法。通过 `self.publish_message(msg)` 发布消息到 MessageBus。Channel 基类已内置自动重连机制。

## Docker 沙箱

```bash
docker build -t personalassistant-sandbox:latest -f Dockerfile.sandbox .
```

```yaml
sandbox:
  enabled: true
```

## MCP 协议

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./data/workspace"]
```

## License

MIT
