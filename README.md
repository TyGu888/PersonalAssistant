# Personal Agent Hub

Pyhton vsersion of OpenClawd

一个可扩展的个人 AI 助手框架，支持多渠道接入、Agent、可插拔 Tools、长期记忆。

## 特性

- **多渠道接入**: CLI / Telegram / Discord / HTTP API
- **多 Agent **: 学习教练、编程助手、通用助手...
- **可插拔 Tools**: 定时提醒、文件操作、Shell 执行、网页搜索、MCP 协议...
- **长期记忆**: Session 历史 (SQLite) + RAG 向量搜索 (ChromaDB) + 跨渠道身份统一
- **Skills 系统**: Anthropic 风格的 Markdown 配置文件
- **Token 管理**: tiktoken 精确计数，智能截断上下文
- **多模态支持**: 图片处理与 Vision API 集成
- **Docker 沙箱**: 容器隔离执行 Shell 命令
- **进程解耦**: Gateway/Agent 分离，故障隔离
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
# CLI 模式（推荐先测试）
python main.py start

# 单次对话测试
python main.py chat "你好"
python main.py chat "我想学习 Python" --agent study_coach
```

## 项目结构

```
personal_agent_hub/
├── main.py                 # CLI 入口
├── config.yaml             # 配置文件
├── core/
│   ├── engine.py           # 主引擎（支持进程解耦）
│   ├── router.py           # 消息路由
│   └── types.py            # 共享类型
├── channels/
│   ├── base.py             # Channel 基类（含自动重连）
│   ├── cli.py              # CLI 交互
│   ├── telegram.py         # Telegram Bot
│   ├── discord.py          # Discord Bot
│   └── http.py             # HTTP API (FastAPI)
├── agents/
│   ├── base.py             # Agent 基类（Token 管理 + 多模态）
│   └── study_coach.py      # 学习教练
├── tools/
│   ├── registry.py         # Tool 注册（支持 MCP）
│   ├── scheduler.py        # 定时提醒
│   ├── filesystem.py       # 文件操作（含 edit/find/grep）
│   ├── shell.py            # Shell 执行（含持久化会话）
│   ├── web.py              # 网页搜索/抓取
│   ├── image.py            # 图片处理
│   ├── sandbox.py          # Docker 沙箱
│   ├── mcp_client.py       # MCP 协议客户端
│   ├── memory.py           # 记忆工具（search/add）
│   └── subagent.py         # Sub-Agent 系统
├── skills/                 # Skills 配置目录
│   ├── loader.py           # Skill 加载器
│   ├── study_coach/SKILL.md
│   ├── default/SKILL.md
│   └── coding_assistant/SKILL.md
├── worker/                 # 进程解耦
│   ├── agent_worker.py     # Agent Worker 进程
│   ├── agent_client.py     # Gateway 端客户端
│   ├── pool.py             # Worker 进程池
│   └── protocol.py         # 通信协议
├── utils/
│   └── token_counter.py    # Token 计数器
├── memory/
│   ├── session.py          # 对话历史 (SQLite)
│   ├── global_mem.py       # 长期记忆 (ChromaDB)
│   └── manager.py          # Memory 管理
├── Dockerfile.sandbox      # 沙箱镜像
└── data/                   # 数据目录（自动创建）
```

## 配置说明

配置文件 `config.yaml` 主要配置项：

```yaml
# LLM 配置
llm:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: ep-20260128095801-jc4gx
  max_context_tokens: 8000    # Token 限制

# 进程模式
engine:
  process_mode: "embedded"    # "embedded" 或 "separated"
  num_workers: 2              # Worker 进程数

# Channel 配置
channels:
  cli:
    enabled: true
  telegram:
    enabled: false
    token: ${TELEGRAM_BOT_TOKEN}
  discord:
    enabled: false
    token: ${DISCORD_BOT_TOKEN}
  http:
    enabled: false
    port: 8080
    api_key: ${HTTP_API_KEY}

# Docker 沙箱
sandbox:
  enabled: false
  image: "personalassistant-sandbox:latest"

# MCP 协议
mcp:
  enabled: false
  servers: []

# 记忆系统
memory:
  identity_mode: "single_owner"  # 跨渠道身份统一
  max_context_messages: 50
  max_context_tokens: 16000
```

## 系统架构

```
用户消息
    │
    ▼
Channel (CLI/Telegram/Discord/HTTP)
    │
    ▼ IncomingMessage
Engine.handle()
    ├── Router.resolve() ──────────► 选择 Agent + Tools
    ├── MemoryManager.get_context() ► 获取历史 + 记忆 (Token 截断)
    ├── Agent.run() ───────────────► LLM 调用 + Tool 执行
    │       │                         (可在 Worker 进程中执行)
    │       ├── LLM 决定调用 Tool
    │       ├── registry.execute() ► 执行 Tool (支持 MCP/沙箱)
    │       └── LLM 生成最终回复
    │
    └── MemoryManager.save() ──────► 保存对话 (SQLite)
    │
    ▼ OutgoingMessage
Channel.send() ► 返回给用户
```

### 进程解耦模式

```
Gateway 进程                    Worker 进程 (×N)
├── Engine                      ├── AgentWorker
├── Channels                    ├── BaseAgent
├── Scheduler                   ├── ToolRegistry
└── AgentClient ──Pipe(IPC)──► └── MemoryManager
```

## 扩展开发

### 添加新 Skill（推荐）

在 `skills/` 目录下创建 `{skill_name}/SKILL.md`：

```markdown
---
name: my_skill
description: 技能描述
metadata:
  emoji: "🎯"
  requires:
    tools: ["tool1", "tool2"]
---

# 角色定义

你是一个...

## 核心职责

- 职责 1
- 职责 2

## 交互风格

语气要...
```

### 添加新 Tool

```python
# tools/my_tool.py
from tools.registry import registry

@registry.register(
    name="my_tool",
    description="工具描述",
    parameters={
        "type": "object",
        "properties": {
            "arg1": {"type": "string", "description": "参数1"}
        },
        "required": ["arg1"]
    }
)
async def my_tool(arg1: str, context=None) -> str:
    engine = context["engine"]  # 依赖注入
    return "结果"
```

### 添加新 Channel

继承 `BaseChannel`，实现 `start()`, `send()`, `stop()` 方法。
Channel 已内置自动重连机制（指数退避 5s → 300s）。

## HTTP API

启用 `channels.http.enabled: true` 后可用：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 发送消息 |
| `/health` | GET | 健康检查 |
| `/agents` | GET | 列出 Agents |
| `/tools` | GET | 列出 Tools |
| `/sessions/{id}` | GET/DELETE | 会话管理 |

```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"text": "你好", "user_id": "user123"}'
```

## Docker 沙箱

1. 构建沙箱镜像：
```bash
docker build -t personalassistant-sandbox:latest -f Dockerfile.sandbox .
```

2. 启用沙箱：
```yaml
sandbox:
  enabled: true
```

## MCP 协议

连接外部 MCP Server 复用社区工具：

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./data/workspace"]
```

## Sub-Agent 系统

Agent 可以生成子 Agent 执行复杂任务：

```python
# Agent 调用 agent_spawn 工具
agent_spawn(
    task="分析这个代码库的架构",
    label="代码分析",
    agent_id="default",
    timeout_seconds=300,
    wait=True  # 同步等待结果
)
```

配套工具：
- `agent_list`: 列出子 Agent 状态
- `agent_send`: 给子 Agent 发消息
- `agent_history`: 获取子 Agent 对话历史

## Memory Tools

Agent 可以主动搜索和添加记忆：

```python
# 搜索记忆
memory_search(query="用户的工作时间偏好", scope="personal")

# 添加记忆
memory_add(
    content="用户喜欢早上 9 点开始工作",
    memory_type="preference",
    scope="personal"
)
```

记忆范围：
- `global`: 环境信息，所有对话可检索（如 channel ID、项目信息）
- `personal`: 用户相关记忆，跨渠道共享

## License

MIT
