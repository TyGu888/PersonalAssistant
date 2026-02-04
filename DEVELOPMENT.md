# Personal Agent Hub - 开发追踪

> 最后更新: 2026-02-03

快速了解项目架构和开发进展。详细技术设计见 `personal-agent-hub-design.md`。

---

## 项目简介

**Personal Agent Hub** 是一个可扩展的个人 AI 助手框架：
- 多渠道接入（CLI / Telegram / Discord / HTTP API）
- 多 Agent 人设（学习教练、编程助手、通用助手...）
- 可插拔 Tools（定时提醒、文件操作、Shell、网页搜索、MCP...）
- 长期记忆（Session 历史 + RAG 向量搜索 + 跨渠道身份统一）
- Skills 系统（Anthropic Markdown 格式配置）
- 进程解耦（Gateway/Agent 分离）
- Docker 沙箱（容器隔离执行）
- Sub-Agent 系统（生成子 Agent 执行复杂任务）

---

## 系统架构

### 目录结构

```
personal_agent_hub/
├── main.py              # CLI 入口
├── config.yaml          # 配置文件
├── Dockerfile.sandbox   # 沙箱镜像
├── core/
│   ├── engine.py        # 主引擎（消息调度 + 进程管理）
│   ├── router.py        # 消息路由（规则匹配）
│   └── types.py         # 共享类型定义
├── channels/
│   ├── base.py          # Channel 基类（含 ReconnectMixin）
│   ├── cli.py           # CLI 交互
│   ├── telegram.py      # Telegram Bot（自动重连）
│   ├── discord.py       # Discord Bot（自动重连）
│   └── http.py          # HTTP API (FastAPI)
├── agents/
│   ├── base.py          # Agent 基类（LLM + Tool + Token 管理 + 多模态）
│   └── study_coach.py   # 学习教练 Agent
├── tools/
│   ├── registry.py      # Tool 注册系统（支持 MCP）
│   ├── scheduler.py     # 智能定时提醒（auto_continue）
│   ├── filesystem.py    # 文件操作（含 edit/find/grep）
│   ├── shell.py         # Shell 命令（含持久化会话）
│   ├── web.py           # 网页搜索 / 抓取
│   ├── image.py         # 图片处理 (Pillow)
│   ├── sandbox.py       # Docker 沙箱
│   ├── mcp_client.py    # MCP 协议客户端
│   ├── memory.py        # 记忆工具（search/add）
│   └── subagent.py      # Sub-Agent 系统
├── skills/              # Skills 配置目录
│   ├── loader.py        # Skill 加载器
│   ├── study_coach/SKILL.md
│   ├── default/SKILL.md
│   └── coding_assistant/SKILL.md
├── worker/              # 进程解耦
│   ├── agent_worker.py  # Agent Worker 进程
│   ├── agent_client.py  # Gateway 端客户端
│   ├── pool.py          # Worker 进程池
│   └── protocol.py      # 通信协议
├── utils/
│   └── token_counter.py # Token 计数器 (tiktoken)
├── memory/
│   ├── session.py       # 对话历史（SQLite）
│   ├── global_mem.py    # 长期记忆（ChromaDB 向量）
│   └── manager.py       # Memory 统一入口
└── data/                # 运行时数据
```

### 消息流

```
Channel (CLI/Telegram/Discord/HTTP)
    │
    ▼ IncomingMessage
Engine.handle()
    ├── Router.resolve() → 选择 Agent + Tools
    ├── MemoryManager.get_context() → 历史 + 记忆 (Token 截断)
    ├── Agent.run() → LLM 处理 + Tool 调用 (可在 Worker 进程执行)
    └── MemoryManager.save() → 保存对话
    │
    ▼ OutgoingMessage
Channel.send() → 用户
```

### 进程解耦架构

```
Gateway 进程 (主进程)               Agent Worker 进程 (子进程 ×N)
├── Engine (消息路由)                ├── AgentWorker (任务接收)
├── Channels (TG/Discord/HTTP)       ├── BaseAgent (LLM 调用)
├── Scheduler                        ├── ToolRegistry (Tool 执行)
└── AgentClient (发任务)  ──IPC──►  └── MemoryManager (记忆访问)
```

---

## 模块说明

| 模块 | 职责 |
|------|------|
| **core/engine.py** | 主引擎，消息调度，Channel 监控重连，进程池管理 |
| **core/router.py** | 根据规则（关键词/正则）决定消息由哪个 Agent 处理 |
| **core/types.py** | 共享数据结构：IncomingMessage, OutgoingMessage 等 |
| **channels/base.py** | Channel 抽象接口 + ReconnectMixin（指数退避重连） |
| **channels/http.py** | FastAPI HTTP API：/chat, /health, /agents, /tools |
| **agents/base.py** | Agent 基类，LLM 调用 + Tool 执行 + Token 管理 + 多模态 |
| **tools/registry.py** | Tool 注册装饰器，支持本地函数和 MCP 工具 |
| **tools/shell.py** | Shell 命令执行 + 持久化会话 (ShellSession) |
| **tools/sandbox.py** | Docker 沙箱，容器隔离执行 |
| **tools/mcp_client.py** | MCP 协议客户端，连接外部 MCP Server |
| **tools/image.py** | 图片处理：压缩、格式转换、Vision API 集成 |
| **tools/memory.py** | 记忆工具：memory_search（主动搜索）、memory_add（主动添加） |
| **tools/subagent.py** | Sub-Agent 系统：agent_spawn、agent_list、agent_send、agent_history |
| **skills/loader.py** | 加载 SKILL.md 文件（Anthropic Markdown 格式） |
| **worker/pool.py** | Worker 进程池，管理 Agent 子进程 |
| **utils/token_counter.py** | tiktoken Token 计数器 |
| **memory/manager.py** | 统一入口，Token 截断上下文 |

---

## 扩展指南

### 添加新 Skill（推荐方式）

在 `skills/` 下创建目录和 `SKILL.md`：

```markdown
---
name: my_skill
description: 技能简短描述
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
from tools.registry import registry

@registry.register(
    name="my_tool",
    description="工具描述",
    parameters={...}
)
async def my_tool(arg1: str, context=None) -> str:
    engine = context["engine"]  # 依赖注入
    return "结果"
```

### 添加新 Channel

继承 `BaseChannel`，Channel 基类已内置自动重连（指数退避 5s → 300s）。

### 使用 MCP 工具

```yaml
mcp:
  enabled: true
  servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "./data"]
```

路由中使用：`tools: [web_search, mcp:*]` 或 `mcp:filesystem:*`

---

## 当前配置

### LLM

```yaml
llm:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: ep-20260128095801-jc4gx
  max_context_tokens: 8000
```

### 进程模式

```yaml
engine:
  process_mode: "embedded"   # "embedded" 或 "separated"
  num_workers: 2
```

- **process_mode**
  - `embedded`：Agent 在主进程里跑，适合开发调试、单机轻量。
  - `separated`：Agent 在独立 Worker 子进程里跑，Gateway 只做路由和 Channel，适合生产、多请求并发。
- **num_workers**（仅 `separated` 时生效）
  - Worker 进程数量。每个请求会分配到一个空闲 Worker 执行（LLM + Tools）。
  - 数量越大：并发能力越强（多用户/多消息同时处理），但内存占用越高（每个 Worker 一份 LLM 客户端和 Tools）。
  - 建议：开发/单用户 1～2，多用户可调到 4 或以上，按机器内存调整。

### 测试指南

大更新后建议按下面顺序测，先保证主流程再测分离模式。

1. **快速单条对话（不启 Worker 池）**
   ```bash
   python main.py chat "你好"
   python main.py chat "提醒我明天 9 点开会"   # 测 scheduler 路由
   ```
   说明：`chat` 命令不会执行 `engine.run()`，因此不会启动 Worker 池；即使配置了 `process_mode: separated`，这一条也会走内嵌逻辑，用于快速验证路由、Agent、Tools 是否正常。

2. **内嵌模式完整跑一遍**
   - 在 `config.yaml` 里设 `process_mode: "embedded"`。
   - 启动服务：`python main.py start`。
   - 用当前已开启的 Channel 发消息（例如 Discord），测多轮对话、定时提醒、Tool 调用等。

3. **分离模式（Worker 池）**
   - 在 `config.yaml` 里设 `process_mode: "separated"`，`num_workers: 2`（或 1 先试）。
   - 启动：`python main.py start`，看日志里是否有 `Starting WorkerPool with N workers`、`WorkerPool started`。
   - 再通过 Discord/Telegram/HTTP 发消息，确认回复正常；可同时发多条或多个会话，观察是否由不同 Worker 处理（日志里会有 worker 相关输出）。

4. **按功能抽查**
   - 学习/复习/督促 → 走 study_coach + scheduler。
   - 搜索/网页 → default + web_search / fetch_url。
   - 执行/命令/沙箱 → run_command、sandbox_*。
   - 提醒/定时 → scheduler_add/list/cancel。
   - 兜底句 → default + 全 tools。

环境变量：确保 `.env` 或本机已设置 `ARK_API_KEY`，若开 Telegram/Discord/HTTP 则对应 token 也需配置。

### 路由规则

| 匹配关键词 | Agent | Tools |
|-----------|-------|-------|
| 学习/复习/督促 | study_coach | scheduler |
| 搜索/查找/网页 | default | web_search, fetch_url |
| 执行/运行/命令/沙箱 | default | run_command, sandbox_* |
| 创建/写入/文件 | default | filesystem 相关 |
| 提醒/定时/闹钟 | default | scheduler 相关 |
| 兜底 | default | 全部 tools |

---

## 开发状态

### 已完成模块

| 类别 | 模块 | 状态 |
|------|------|------|
| **Core** | engine, router, types | ✅ |
| **Channels** | CLI, Telegram, Discord, HTTP | ✅ |
| **Agents** | base (Token + 多模态), study_coach | ✅ |
| **Tools** | registry, scheduler, filesystem, shell, web, image, sandbox, mcp_client, discord, memory, subagent | ✅ |
| **Memory** | session, global_mem (scope + person_id), manager (Token 截断 + Identity Mapping) | ✅ |
| **Skills** | loader, study_coach, default, coding_assistant | ✅ |
| **Worker** | agent_worker, agent_client, pool, protocol | ✅ |
| **Utils** | token_counter | ✅ |

### 已验证功能

- [x] CLI 模式对话
- [x] 火山引擎 API 调用（OpenAI 兼容）
- [x] 消息路由（关键词匹配）
- [x] Tool 注册与执行（依赖注入）
- [x] 对话历史保存（SQLite）
- [x] 向量记忆搜索（ChromaDB）
- [x] 智能定时提醒（auto_continue）
- [x] Discord / Telegram Bot 集成
- [x] Channel 自动重连（指数退避）
- [x] Skills 配置化加载
- [x] Token 精确计数与截断
- [x] HTTP API (FastAPI)
- [x] 多模态图片处理
- [x] 持久化 Shell 会话
- [x] Docker 沙箱执行
- [x] MCP 协议接入
- [x] 进程解耦（Gateway/Agent 分离）
- [x] 世界信息传递（channel, user_id, timestamp, is_owner）
- [x] NO_REPLY 机制（Agent 可选择不回复）
- [x] Channel Tools（channel_tools 配置自动加载）
- [x] Discord Tools（send, reply, reaction, thread）
- [x] 跨渠道身份统一（Identity Mapping: user_id → person_id）
- [x] 记忆分层（Memory Scope: global + personal）
- [x] 文件精确编辑（edit_file, find_files, grep_files）
- [x] Memory Tools（Agent 主动搜索/添加记忆）
- [x] Sub-Agent 系统（agent_spawn, agent_list, agent_send, agent_history）

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-02-03 | Sub-Agent 系统：agent_spawn、agent_list、agent_send、agent_history |
| 2026-02-03 | Memory Tools：memory_search（主动搜索）、memory_add（主动添加） |
| 2026-02-03 | Memory 框架重构：Identity Mapping（跨渠道身份统一）+ Memory Scope（global/personal） |
| 2026-02-03 | 文件工具增强：edit_file（精确替换）、find_files（Glob 查找）、grep_files（内容搜索） |
| 2026-01-31 | Channel-Agent 架构改进：世界信息传递、Owner 识别、NO_REPLY 机制 |
| 2026-01-31 | Channel Tools 机制：channel_tools 配置，自动合并到路由 |
| 2026-01-31 | Discord Tools：send_message, reply_message, add_reaction, create_thread |
| 2026-01-30 | 进程解耦：Gateway/Agent 分离，Worker 进程池 |
| 2026-01-30 | MCP 协议接入：连接外部 MCP Server |
| 2026-01-30 | Docker 沙箱：容器隔离执行 Shell 命令 |
| 2026-01-30 | HTTP API：FastAPI 实现 RESTful 接口 |
| 2026-01-30 | 多模态支持：Pillow 图片处理 + Vision API |
| 2026-01-30 | 持久化 Shell：有状态 Shell 会话 |
| 2026-01-30 | Token 管理：tiktoken 精确计数与截断 |
| 2026-01-30 | Skills 配置化：Anthropic Markdown 格式 |
| 2026-01-30 | Channel 自动重连：指数退避重连机制 |
| 2026-01-29 | Scheduler 智能化：`auto_continue` 参数 |
| 2026-01-29 | 新增 Discord Channel |
| 2026-01-28 | 初始版本，完成 Phase 0 核心功能 |

---

## 依赖库

```
typer>=0.9.0
python-dotenv>=1.0.0
python-telegram-bot>=20.0
discord.py>=2.3.0
openai>=1.0.0
chromadb>=0.4.0
apscheduler>=3.10.0
pyyaml>=6.0
python-dateutil>=2.8.0
ddgs>=7.0.0
httpx>=0.24.0
beautifulsoup4>=4.12.0
Pillow>=10.0.0
tiktoken>=0.5.0
fastapi>=0.100.0
uvicorn>=0.23.0
docker>=6.0.0
```

---

## 未来方向

### 短期

| 方向 | 说明 |
|------|------|
| 智能路由 | 用 LLM (Meta-Agent) 动态决定路由 |
| 微信 Channel | 个人微信 / 企业微信接入 |
| cron 增强 | 完整 cron 表达式、recurring jobs |
| 后台进程管理 | process_start, process_list, process_kill |
| 统一消息工具 | 跨渠道 message 工具 |
| 无头浏览器 | browser_* (Playwright) |

---

## 已完成: Tool 能力对齐 clawdbot (2026-02-03)

> 目标：将 PersonalAssistant 的工具能力与 clawdbot 对齐，实现操作系统级的 Agent 能力

### 已实现工具

| 类别 | 工具 | 说明 |
|------|------|------|
| **文件操作** | `edit_file` | 精确字符串替换（类似 StrReplace） |
| **文件操作** | `find_files` | Glob 模式查找文件 |
| **文件操作** | `grep_files` | 搜索文件内容（正则 + 上下文） |
| **记忆** | `memory_search` | Agent 主动搜索记忆（支持 scope 过滤） |
| **记忆** | `memory_add` | Agent 主动添加记忆（支持 global/personal） |
| **Sub-Agent** | `agent_spawn` | 生成子 Agent 执行任务（同步/异步） |
| **Sub-Agent** | `agent_list` | 列出子 Agent 状态 |
| **Sub-Agent** | `agent_send` | 给子 Agent 发消息 |
| **Sub-Agent** | `agent_history` | 获取子 Agent 对话历史 |

### 待实现工具

| 类别 | 工具 | 说明 |
|------|------|------|
| **定时任务** | `cron_*` | 完整 cron 表达式支持 |
| **进程管理** | `process_*` | 后台进程管理 |
| **消息** | `message` | 统一跨渠道消息工具 |
| **浏览器** | `browser_*` | 无头浏览器控制（Playwright） |

---

## 已完成: 记忆框架重构 (2026-02-03)

### 实现内容

**1. Identity Mapping（跨渠道身份统一）**

- 配置：`memory.identity_mode: "single_owner"` 或 `"multi_user"`
- `single_owner` 模式：所有 allowed_users 映射到同一个 `person_id`（"owner"）
- 代码：`core/engine.py` 添加 `_resolve_person_id()` 方法

**2. Memory Scope（记忆分层）**

- `global`：环境信息，所有对话可检索
- `personal`：用户相关记忆，跨渠道共享
- 代码改动：
  - `core/types.py`：MemoryItem 添加 `person_id` 和 `scope` 字段
  - `memory/global_mem.py`：add/search 支持 scope 过滤
  - `memory/manager.py`：get_context 使用 person_id

**3. Memory Tools**

- `memory_search`：Agent 主动搜索记忆
- `memory_add`：Agent 主动添加记忆

### 配置示例

```yaml
memory:
  identity_mode: "single_owner"  # "single_owner" | "multi_user"
  max_context_messages: 50
  max_context_tokens: 16000
```

### 未来扩展：Team Assistant

支持多 person_id 映射：
```yaml
identity:
  mapping:
    - person_id: alice
      channels:
        discord: ["123456"]
        telegram: ["789"]
    - person_id: bob
      channels:
        discord: ["654321"]
```

### 中期

| 方向 | 说明 |
|------|------|
| 动态 Prompt | 根据任务类型、用户历史动态生成 prompt |
| 插件系统 | Channel/Tool 作为独立包动态加载 |
| Web 前端 | 管理界面 + 对话 UI |

### 长期

| 方向 | 说明 |
|------|------|
| Multi-Agent | 多 Agent 协作（Planner → Coder → Reviewer） |
| 分布式部署 | Gateway 云端 + Agent 本地 |
