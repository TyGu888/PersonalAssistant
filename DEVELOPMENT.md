# Personal Agent Hub - 开发追踪

> 最后更新: 2026-02-06

快速了解项目架构和开发进展。

---

## 项目简介

**Personal Agent Hub** 是一个 Agent-Centric 的个人 AI 助手框架：
- Agent 是系统核心主体，自主管理记忆和决策
- Gateway 中心枢纽（FastAPI + WebSocket + MessageBus）
- 多渠道接入（Telegram / Discord / WebSocket CLI Client）
- 插件式 Skills 系统（Agent 按需加载 SKILL.md）
- 可插拔 Tools（定时提醒、文件操作、Shell、网页搜索、MCP、跨渠道消息...）
- 长期记忆（Session 历史 + RAG 向量搜索 + 跨渠道身份统一）
- 进程解耦（Gateway/Agent 分离，Worker 进程池）
- Docker 沙箱（容器隔离执行）
- Sub-Agent 系统（生成子 Agent 执行复杂任务）

---

## 系统架构

### 目录结构

```
personal_agent_hub/
├── main.py                    # CLI 入口（start/chat/client）
├── config.yaml                # 配置文件
├── Dockerfile.sandbox         # 沙箱镜像
├── gateway/                   # Gateway 中心枢纽
│   ├── app.py                 # Gateway 主类（系统入口）
│   ├── bus.py                 # MessageBus（Inbox 异步队列）
│   ├── dispatcher.py          # 出站消息路由（Channel/WebSocket）
│   ├── channel_manager.py     # Channel 生命周期（启动/监控/重启）
│   └── server.py              # FastAPI + WebSocket 服务
├── agent/                     # Agent 运行时
│   ├── loop.py                # AgentLoop（事件驱动主循环 + 周期性唤醒）
│   ├── runtime.py             # AgentRuntime（Memory 管理 + 身份解析）
│   ├── base.py                # BaseAgent（LLM 调用 + Tool 执行 + Token 管理）
│   └── default.py             # DefaultAgent（通用助手 + Skill 清单注入）
├── channels/                  # Channel Services（独立通讯服务）
│   ├── base.py                # Channel 基类（MessageBus 集成 + ReconnectMixin）
│   ├── cli.py                 # CLI Channel（本地调试）
│   ├── telegram.py            # Telegram Bot（自动重连）
│   └── discord.py             # Discord Bot（自动重连）
├── cli_client/                # 远程 CLI 客户端
│   └── client.py              # WebSocket CLI（类 Claude Code 风格）
├── tools/                     # 可插拔工具
│   ├── registry.py            # Tool 注册系统（支持 MCP）
│   ├── channel.py             # 跨渠道消息发送（send_message）
│   ├── scheduler.py           # 智能定时提醒（auto_continue）
│   ├── filesystem.py          # 文件操作（edit/find/grep，支持 skills/ 和 data/）
│   ├── shell.py               # Shell 命令（持久化会话）
│   ├── web.py                 # 网页搜索 / 抓取
│   ├── image.py               # 图片处理 (Pillow)
│   ├── sandbox.py             # Docker 沙箱
│   ├── mcp_client.py          # MCP 协议客户端
│   ├── memory.py              # 记忆工具（search/add）
│   └── subagent.py            # Sub-Agent 系统
├── skills/                    # 插件式 Skills（Agent 按需加载）
│   ├── loader.py              # Skill 加载器 + get_skill_summaries()
│   ├── study_coach/SKILL.md
│   ├── coding_assistant/SKILL.md
│   └── project_manager/SKILL.md
├── worker/                    # 进程解耦
│   ├── agent_worker.py        # Agent Worker 进程（使用 AgentRuntime）
│   ├── agent_client.py        # Gateway 端客户端
│   ├── pool.py                # Worker 进程池
│   └── protocol.py            # 通信协议
├── core/                      # 共享模块
│   ├── types.py               # 类型定义（Message, Envelope, Route 等）
│   └── router.py              # 消息路由（选择 Tools）
├── memory/                    # 记忆系统
│   ├── session.py             # 对话历史（SQLite）
│   ├── global_mem.py          # 长期记忆（ChromaDB 向量）
│   └── manager.py             # Memory 统一入口
├── utils/
│   └── token_counter.py       # Token 计数器 (tiktoken)
└── data/                      # 运行时数据（含 state/ 状态文件）
```

### 消息流（Agent-Centric）

```
Channel/Client 收到消息
    │
    ▼ publish(IncomingMessage)
MessageBus (asyncio.Queue Inbox)
    │
    ▼ consume() / consume_timeout()
AgentLoop
    ├── AgentRuntime.save_message("user", ...) → 保存用户消息
    ├── Router.resolve() → 选择 Tools
    ├── AgentRuntime.load_context() → 历史 + 记忆 (Token 截断)
    ├── BaseAgent.run() → LLM 处理 + Tool 调用
    │       └── 根据 Skill 清单按需加载 SKILL.md
    ├── AgentRuntime.save_message("assistant", ...) → 保存回复
    └── Dispatcher.dispatch_reply(envelope, response)
          ├── reply_future.set_result() → 同步客户端（HTTP/WS）
          └── channel.send() → 异步渠道（Discord/Telegram）
```

### 与旧架构对比

```
旧架构: Channel ──callback──► Engine.handle() ──► Agent.run()
                               (Engine 管 Memory)

新架构: Channel ──publish──► MessageBus ──► AgentLoop ──► Dispatcher
                                           (Agent 管 Memory)
```

| 概念 | 旧（Engine-Centric） | 新（Agent-Centric） |
|------|----------------------|---------------------|
| 中心 | Engine | Agent (AgentLoop) |
| 消息传递 | 回调函数 (on_message) | MessageBus (asyncio.Queue) |
| Memory 归属 | Engine 管理 | Agent 自己管理 (AgentRuntime) |
| Channel 角色 | 主动调用 Engine | 独立服务，发布到 Bus |
| HTTP/WS | HTTPChannel (独立 Channel) | GatewayServer (Gateway 的一部分) |
| CLI | 内嵌 Channel | 独立 WebSocket Client |
| 主动发消息 | 无 | send_message Tool |

---

## 模块说明

| 模块 | 职责 |
|------|------|
| **gateway/app.py** | Gateway 主类，初始化和管理所有组件的生命周期 |
| **gateway/bus.py** | MessageBus，Inbox 异步队列 + MessageEnvelope（含 reply Future） |
| **gateway/dispatcher.py** | 出站消息路由，注册 Channel send 函数和 WebSocket 连接 |
| **gateway/channel_manager.py** | Channel 创建、Bus 注入、启动监控、指数退避重启 |
| **gateway/server.py** | FastAPI 服务：POST /chat, WS /ws, 管理端点 |
| **agent/loop.py** | Agent 事件驱动主循环，从 Bus 取消息，调用 Agent，分发回复 |
| **agent/runtime.py** | Agent 运行时：持有 MemoryManager，加载上下文，身份解析 |
| **agent/base.py** | BaseAgent：LLM 调用 + Tool 执行 + Token 管理 + 多模态 |
| **agent/default.py** | DefaultAgent：通用助手，Skill 清单注入 |
| **channels/base.py** | Channel 基类：publish_message() + ReconnectMixin |
| **tools/channel.py** | send_message 工具：Agent 主动向任意 Channel 发消息 |
| **tools/registry.py** | Tool 注册装饰器，支持本地函数和 MCP 工具 |
| **cli_client/client.py** | WebSocket CLI 客户端，类 Claude Code 风格 |
| **worker/agent_worker.py** | Worker 进程，使用 AgentRuntime 替代直接 MemoryManager |
| **core/types.py** | 共享类型：IncomingMessage, OutgoingMessage, MessageEnvelope |
| **core/router.py** | 消息路由（选择 Tools，Agent 统一为 default） |
| **skills/loader.py** | 插件式 Skill 加载器，get_skill_summaries() |
| **memory/manager.py** | Memory 统一入口，Token 截断上下文 |

---

## 扩展指南

### 添加新 Skill（插件式）

在 `skills/` 下创建目录和 `SKILL.md`：

```markdown
---
name: my_skill
description: 技能简短描述（会显示在 Skill 清单中）
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
```

**状态文件**：Skill 可以在 `data/state/` 目录下维护状态文件，通过 filesystem 工具读写。

### 添加新 Tool

```python
from tools.registry import registry

@registry.register(
    name="my_tool",
    description="工具描述",
    parameters={...}
)
async def my_tool(arg1: str, context=None) -> str:
    runtime = context["runtime"]    # AgentRuntime 引用
    dispatcher = context["dispatcher"]  # Dispatcher 引用
    memory = context["memory"]      # MemoryManager 引用
    return "结果"
```

### 添加新 Channel

继承 `BaseChannel`，实现 `start()`, `send()`, `stop()`。使用 `self.publish_message(msg)` 发布到 MessageBus。ChannelManager 自动注入 Bus 和注册 Dispatcher。

---

## 当前配置

### LLM

```yaml
llm_profiles:
  ark_doubao:
    api_key: ${ARK_API_KEY}
    base_url: https://ark.cn-beijing.volces.com/api/v3
    model: ep-xxx
    extra_params:
      reasoning_effort: medium

llm:
  active: ark_doubao
  max_context_tokens: 16000
```

### Gateway

```yaml
gateway:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  api_key: ${HTTP_API_KEY}
```

### Agent

```yaml
agent:
  wake_interval: 0  # 周期性唤醒（秒），0 = 仅事件驱动
```

### 测试指南

1. **快速单条对话**
   ```bash
   python main.py chat "你好"
   ```

2. **完整启动（Gateway + Channels + Agent）**
   ```bash
   python main.py start
   ```
   通过 Discord 发消息测试多轮对话、Tool 调用等。

3. **CLI Client 测试（WebSocket）**
   - 终端 1: `python main.py start`
   - 终端 2: `python main.py client`

4. **按功能抽查**
   - 学习/复习 → DefaultAgent 加载 study_coach Skill
   - 搜索/网页 → web_search / fetch_url
   - 执行/命令 → run_command, sandbox_*
   - 提醒/定时 → scheduler_add/list/cancel
   - 跨渠道发消息 → send_message Tool
   - 项目管理 → DefaultAgent 加载 project_manager Skill

### 路由规则

| 消息 | Agent | Tools |
|------|-------|-------|
| 所有消息 | default | 全部 tools（含 send_message） |

> Agent 根据 Skill 清单按需加载能力。

---

## 开发状态

### 已完成模块

| 类别 | 模块 | 状态 |
|------|------|------|
| **Gateway** | app, bus, dispatcher, channel_manager, server | ✅ |
| **Agent** | loop, runtime, base, default | ✅ |
| **Channels** | CLI, Telegram, Discord | ✅ |
| **Tools** | registry, channel, scheduler, filesystem, shell, web, image, sandbox, mcp_client, memory, subagent | ✅ |
| **Memory** | session, global_mem (scope + person_id), manager (Token 截断 + Identity Mapping) | ✅ |
| **Skills** | loader (插件式), study_coach, coding_assistant, project_manager | ✅ |
| **Worker** | agent_worker (使用 AgentRuntime), agent_client, pool, protocol | ✅ |
| **CLI Client** | WebSocket CLI (类 Claude Code) | ✅ |
| **Core** | types (含 MessageEnvelope), router | ✅ |
| **Utils** | token_counter | ✅ |

### 已验证功能

- [x] Gateway 构造和组件初始化
- [x] MessageBus：publish/consume/wait_reply/timeout
- [x] Dispatcher：Channel 路由 + Future 回复
- [x] 端到端消息流（Gateway → AgentLoop → Agent → Dispatcher）
- [x] 火山引擎/DeepSeek API 调用（OpenAI 兼容）
- [x] 消息路由
- [x] Tool 注册与执行（依赖注入）
- [x] 对话历史保存（SQLite）
- [x] 向量记忆搜索（ChromaDB）
- [x] 智能定时提醒（auto_continue）
- [x] Discord / Telegram Bot 集成
- [x] Channel 自动重连（指数退避）
- [x] Skills 插件式加载
- [x] Token 精确计数与截断
- [x] FastAPI + WebSocket Gateway
- [x] 多模态图片处理
- [x] 持久化 Shell 会话
- [x] Docker 沙箱执行
- [x] MCP 协议接入
- [x] 世界信息传递（channel, user_id, timestamp, is_owner）
- [x] NO_REPLY 机制
- [x] Channel Tools（channel_tools 配置自动加载）
- [x] 跨渠道身份统一（Identity Mapping）
- [x] 记忆分层（Memory Scope: global + personal）
- [x] Memory Tools（Agent 主动搜索/添加记忆）
- [x] Sub-Agent 系统
- [x] send_message Tool（Agent 主动跨渠道发消息）
- [x] CLI Client（WebSocket 连接 Gateway）

### 待测试

- [ ] Discord Channel 在新架构下的完整对话
- [ ] CLI Client 连接 Gateway 交互
- [ ] 周期性唤醒 (wake_interval > 0)
- [ ] Worker 分离模式在新架构下运行

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-02-06 | **架构重构：Agent-Centric**。MessageBus 解耦 Channel 和 Agent；Agent 自主管理 Memory；Gateway 替代 Engine；FastAPI + WebSocket 服务；CLI Client；send_message Tool |
| 2026-02-05 | Skill 系统重构：从 Agent 替换模式改为插件式按需加载 |
| 2026-02-05 | Filesystem 路径扩展：支持访问 skills/ 和 data/ 目录 |
| 2026-02-05 | 简化路由：所有消息统一给 DefaultAgent + 全部 Tools |
| 2026-02-04 | 多 Provider Profile：llm_profiles 配置 |
| 2026-02-04 | Project Manager Skill |
| 2026-02-03 | Sub-Agent 系统 + Memory Tools + 记忆框架重构 |
| 2026-02-03 | 文件工具增强：edit_file、find_files、grep_files |
| 2026-01-31 | Channel-Agent 架构改进：世界信息、Owner 识别、NO_REPLY |
| 2026-01-30 | 进程解耦 + MCP + Docker 沙箱 + HTTP API + 多模态 |
| 2026-01-29 | Scheduler 智能化 + Discord Channel |
| 2026-01-28 | 初始版本 |

---

## 已删除的旧文件

以下文件在 Agent-Centric 重构中被替代和删除：

| 旧文件 | 替代为 |
|--------|--------|
| `core/engine.py` | `gateway/app.py` |
| `channels/http.py` | `gateway/server.py` |
| `agents/base.py` | `agent/base.py` |
| `agents/study_coach.py` | `agent/default.py` |

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
websockets>=12.0
docker>=6.0.0
```

---

## 未来方向

### 短期

| 方向 | 说明 |
|------|------|
| 智能路由 | 用 LLM (Meta-Agent) 动态决定路由 |
| 唤醒 Agent | 小 LLM 控制主 Agent 是否唤醒（待评估） |
| 微信 Channel | 个人微信 / 企业微信接入 |
| cron 增强 | 完整 cron 表达式、recurring jobs |
| 后台进程管理 | process_start, process_list, process_kill |
| 无头浏览器 | browser_* (Playwright) |
| Mac/iOS Client | 远程 Client 通过 WebSocket 执行本地操作 |

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
| 图记忆 | Knowledge Graph 增强记忆系统 |
