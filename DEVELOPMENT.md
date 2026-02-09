# Personal Agent Hub - 开发追踪

> 最后更新: 2026-02-07

快速了解项目架构和开发进展。

---

## 项目简介

**Personal Agent Hub** 是一个 Agent-Centric 的个人 AI 助手框架：
- Agent 是系统核心主体，自主管理记忆和决策
- Gateway 中心枢纽（FastAPI + WebSocket + MessageBus）
- 多渠道接入（Discord / Telegram / Slack / 飞书 / QQ / WebSocket CLI Client）
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
├── main.py                    # CLI 入口（start/client）
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
│   ├── telegram.py            # Telegram Bot（自动重连）
│   ├── discord.py             # Discord Bot（自动重连）
│   ├── slack.py               # Slack Bot（Socket Mode）
│   ├── feishu.py              # 飞书 Bot（WebSocket）
│   └── qq.py                  # QQ Bot（频道/群/C2C）
├── cli_client/                # 远程 CLI 客户端
│   └── client.py              # WebSocket CLI（类 Claude Code 风格）
├── tools/                     # 可插拔工具
│   ├── registry.py            # Tool 注册系统（支持 MCP）
│   ├── channel.py             # 跨渠道消息发送（send_message）
│   ├── discord_actions.py     # Discord 特定操作（回复/反应/建线程）
│   ├── slack_actions.py       # Slack 特定操作（Thread 回复/反应/置顶）
│   ├── feishu_actions.py      # 飞书特定操作（回复/反应/置顶/建群）
│   ├── qq_actions.py          # QQ 特定操作（反应/置顶）
│   ├── scheduler.py           # 智能定时提醒（auto_continue）
│   ├── filesystem.py          # 文件操作（edit/find/grep，支持 skills/ 和 data/）
│   ├── shell.py               # Shell 命令 + 持久化会话 + Docker 沙箱管理
│   ├── web.py                 # 网页搜索 / 抓取
│   ├── image.py               # 图片处理 (Pillow)
│   ├── sandbox.py             # Docker 沙箱基础设施（DockerSandbox 类，无工具注册）
│   ├── mcp_client.py          # MCP 协议客户端
│   ├── memory.py              # 记忆工具（search/add）
│   └── subagent.py            # Sub-Agent 系统（已禁用，待迁移到 MessageBus）
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
          └── channel.deliver(target, msg) → 异步渠道（Discord/Telegram）
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
| **gateway/dispatcher.py** | 出站消息路由，注册 Channel deliver 函数和 WebSocket 连接 |
| **gateway/channel_manager.py** | Channel 创建、Bus 注入、启动监控、指数退避重启 |
| **gateway/server.py** | FastAPI 服务：POST /chat, WS /ws, 管理端点 |
| **agent/loop.py** | Agent 事件驱动主循环，从 Bus 取消息，调用 Agent，分发回复 |
| **agent/runtime.py** | Agent 运行时：持有 MemoryManager，加载上下文，身份解析 |
| **agent/base.py** | BaseAgent：LLM 调用 + Tool 执行 + Token 管理 + 多模态 |
| **agent/default.py** | DefaultAgent：通用助手，Skill 清单注入 |
| **channels/base.py** | Channel 基类：publish_message() (fire-and-forget) + deliver(target, msg) + ReconnectMixin |
| **channels/slack.py** | Slack Bot (Socket Mode + AsyncApp) |
| **channels/feishu.py** | 飞书 Bot (WebSocket + lark.ws.Client) |
| **channels/qq.py** | QQ Bot (频道/群/C2C, botpy.Client) |
| **tools/channel.py** | send_message 工具：Agent 主动向任意 Channel 发消息 |
| **tools/registry.py** | Tool 注册装饰器，支持本地函数和 MCP 工具 |
| **tools/shell.py** | Shell 命令 (run_command + 持久化会话) + Docker 沙箱管理 (stop/status/copy) |
| **tools/sandbox.py** | Docker 沙箱基础设施（DockerSandbox 类），无工具注册，被 shell.py 调用 |
| **tools/subagent.py** | Sub-Agent 系统（已禁用，待迁移到 MessageBus） |
| **tools/slack_actions.py** | Slack Thread 回复、反应、置顶 |
| **tools/feishu_actions.py** | 飞书消息回复、反应、置顶、建群 |
| **tools/qq_actions.py** | QQ 表情反应、消息置顶 |
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

继承 `BaseChannel`，实现 `start()`, `deliver(target, msg)`, `stop()`。使用 `self.publish_message(msg)` 发布到 MessageBus。ChannelManager 自动注入 Bus 和注册 Dispatcher。

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

1. **完整启动（Gateway + Channels + Agent）**
   ```bash
   python main.py start
   ```
   通过 Discord 发消息测试多轮对话、Tool 调用等。

2. **CLI Client 测试（WebSocket）**
   - 终端 1: `python main.py start`
   - 终端 2: `python main.py client`

3. **按功能抽查**
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
| **Channels** | Telegram, Discord, Slack, Feishu, QQ | ✅ |
| **Tools** | registry, channel, scheduler (含 SQLite 持久化), filesystem, shell (含沙箱管理), web, image, browser (Playwright), sandbox (基础设施), mcp_client, memory | ✅ |
| **Tools (禁用)** | subagent（待迁移到 MessageBus） | ⏸️ |
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
- [x] Unified deliver pattern（Dispatcher → channel.deliver）
- [x] WebSocket RPC（CLI Client 提供工具给 Agent）
- [x] System wake messages（周期性唤醒 + 定时任务唤醒）
- [x] Contact Registry（启动扫描 + 懒积累）
- [x] 通讯录注入 system prompt（唤醒消息时显示）

### 待测试

- [ ] Discord Channel deliver 模式完整对话
- [ ] Telegram Channel deliver 模式完整对话  
- [ ] CLI Client WebSocket RPC 工具调用
- [ ] 周期性唤醒 (wake_interval > 0)
- [ ] Scheduler 唤醒 Agent 后使用 send_message 投递
- [ ] Worker 分离模式在新架构下运行
- [ ] Slack Channel 完整对话测试
- [ ] 飞书 Channel 完整对话测试
- [ ] QQ Channel 完整对话测试（频道/群/C2C）
- [ ] Contact Registry 启动扫描验证
- [ ] 周期性唤醒通讯录可见性验证

### 运行中已知问题（可选优化）

| 现象 | 说明与建议 |
|------|------------|
| LLM 120s/182s 超时 | 已支持 `config.agent.llm_call_timeout`（默认 120）。若仍超时，可适当调大或检查模型侧延迟。 |
| 同渠道多会话并发 | Slack 已按 thread_id 隔离 session；若多 thread 同时进消息会串行处理。如需严格串行可按 channel+thread 加锁（未实现）。 |
| Ctrl+C 时 posthog atexit 报错 | 本仓库未依赖 posthog；若出现多为 IDE/环境注入。可在 main 的 signal 处理里忽略 atexit 阶段的 KeyboardInterrupt（按需）。 |

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-02-07 | **Scheduler 持久化 + Browser 工具**。定时提醒使用 SQLite jobstore（data/scheduler.db），重启后任务保留；回调改为模块级 `run_scheduled_reminder` 以支持序列化。新增 browser_*（Playwright）：browser_open/goto/click/fill/snapshot/close，需 `playwright install chromium`。 |
| 2026-02-07 | **Tool 清理 + Wake 机制修复**。禁用 subagent 工具（待迁移 MessageBus）；sandbox 工具合并到 shell.py（移除冗余 sandbox_exec/sandbox_start，sandbox.py 保留为纯基础设施）；修复周期性唤醒：不加载对话历史（防污染）、保留 memories、限制 max_iterations=3、跳过并发 wake、通讯录概要注入普通对话 |
| 2026-02-07 | **新增 Slack/飞书/QQ Channel + Contact Registry**。三个新渠道完整接入（收发消息、deliver 模式、平台特有操作工具）；Contact Registry 通讯录系统（启动扫描 + 懒积累 + 唤醒时注入 system prompt）|
| 2026-02-07 | **统一出站路径重构**。Channel.send() → deliver(target, msg)；Dispatcher 统一路由回复和主动消息；删除 CLI Channel（cli_client 替代）；Scheduler 回调改为 MessageBus 唤醒；Agent 周期性唤醒发布系统消息；WebSocket RPC 支持远程工具调用 |
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
| `channels/cli.py` | `cli_client/client.py` |
| `tools/discord.py` | `tools/discord_actions.py` |

---

## 依赖库

```
typer>=0.9.0
python-dotenv>=1.0.0
python-telegram-bot>=20.0
discord.py>=2.3.0
slack-bolt[async]>=1.18.0
lark-oapi>=1.5.0
qq-botpy>=1.1.5
openai>=1.0.0
chromadb>=0.4.0
apscheduler>=3.10.0
sqlalchemy>=2.0.0
playwright>=1.40.0
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
| 无头浏览器 | browser_* (Playwright)（已实现） |
| Mac/iOS Client | 远程 Client 通过 WebSocket 执行本地操作 |

### 中期

| 方向 | 说明 |
|------|------|
| 动态 Prompt | 根据任务类型、用户历史动态生成 prompt |
| 插件系统 | Channel/Tool 作为独立包动态加载 |
| Web 前端 | 管理界面 + 对话 UI |
mac，ios，window，linux系统的系统工具？以及可能的截图，点击操作？

### 长期

| 方向 | 说明 |
|------|------|
| Multi-Agent | 多 Agent 协作（Planner → Coder → Reviewer） |
| 分布式部署 | Gateway 云端 + Agent 本地 |
| 图记忆 | Knowledge Graph 增强记忆系统 |
