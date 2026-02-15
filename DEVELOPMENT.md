# Personal Agent Hub - 开发追踪

> 最后更新: 2026-02-15

快速了解项目架构和开发进展。

---

## 项目简介

**Personal Agent Hub** 是一个 Agent-Centric 的个人 AI 助手框架：
- Agent 是系统核心主体，自主管理记忆和决策
- Gateway 中心枢纽（FastAPI + WebSocket + MessageBus）
- 多渠道接入（Discord / Telegram / Slack / 飞书 / QQ / 企业微信 / WebSocket CLI Client）
- 插件式 Skills 系统（Agent 按需加载 SKILL.md）
- 可插拔 Tools（定时提醒、文件操作、Shell、网页搜索、MCP、跨渠道消息、Computer Use...）
- 长期记忆（Session 历史 + RAG 向量搜索 + 跨渠道身份统一）
- 进程解耦（Gateway/Agent 分离，Worker 进程池）
- Docker 沙箱（容器隔离执行）
- 动态 Sub-Agent 系统（主 Agent 即时定义 prompt/tools/model，前台+后台模式）
- 运行时热更新（切换 LLM Profile、重载 Skills、动态 MCP 连接）

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
│   ├── scheduler.py           # 定时提醒 scheduler(action=add/list/cancel)
│   ├── filesystem.py          # 文件操作（edit/find/grep，支持 skills/ 和 data/）
│   ├── shell.py               # run_command + shell_session(action=...) + sandbox(action=...)
│   ├── web.py                 # 网页搜索 / 抓取
│   ├── image.py               # 图片处理 (Pillow)
│   ├── sandbox.py             # Docker 沙箱基础设施（DockerSandbox 类，无工具注册）
│   ├── mcp_client.py          # MCP 协议客户端
│   ├── memory.py              # memory(action=search/add)
│   ├── subagent.py            # agent(action=spawn/list/query/send/stop/history)
│   ├── config_manager.py      # config(action=get/set/switch_profile/reload_skills)
│   ├── mcp_tools.py           # mcp(action=connect/disconnect/list)
│   ├── computer_use.py        # Computer Use 工具注册（computer_action + 低层 GUI 工具）
│   └── computer/              # Computer Use 内部模块
│       ├── actions.py         # ActionBackend（PyAutoGUI/screencapture 封装）
│       ├── memory.py          # ActionMemory（滑动窗口截图 + 文本动作历史）
│       └── grounding.py       # GroundingEngine（自主 GUI 任务执行器 + VisionLLM 后端）
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
| **agent/base.py** | BaseAgent：LLM 调用 + Tool 执行 + Token 管理 + 多模态 + Tool result 图片自动检测 |
| **agent/default.py** | DefaultAgent：通用助手，Skill 清单注入 |
| **channels/base.py** | Channel 基类：publish_message() (fire-and-forget) + deliver(target, msg) + ReconnectMixin |
| **channels/slack.py** | Slack Bot (Socket Mode + AsyncApp) |
| **channels/feishu.py** | 飞书 Bot (WebSocket + lark.ws.Client) |
| **channels/qq.py** | QQ Bot (频道/群/C2C, botpy.Client) |
| **channels/wecom.py** | 企业微信自建应用（HTTP 回调 + access_token，GET/POST /wecom/callback） |
| **channels/wecom_crypto.py** | 企业微信消息加解密 (WXBizMsgCrypt) |
| **tools/channel.py** | send_message 工具：Agent 主动向任意 Channel 发消息 |
| **tools/registry.py** | Tool 注册装饰器，支持本地函数和 MCP 工具 |
| **tools/shell.py** | run_command（沙箱感知）+ shell_session(action=start/exec/stop/list) + sandbox(action=status/stop/copy_to/copy_from) |
| **tools/sandbox.py** | Docker 沙箱基础设施（DockerSandbox 类），无工具注册，被 shell.py 调用 |
| **tools/browser.py** | browser(action=open/goto/click/fill/snapshot/screenshot/close)，Playwright 无头浏览器 |
| **tools/subagent.py** | agent(action=spawn/list/query/send/stop/history)，动态 Sub-Agent |
| **tools/config_manager.py** | config(action=get/set/switch_profile/reload_skills)，运行时配置热更新 |
| **tools/mcp_tools.py** | mcp(action=connect/disconnect/list)，MCP 动态热插拔 |
| **tools/slack_actions.py** | Slack Thread 回复、反应、置顶 |
| **tools/feishu_actions.py** | 飞书消息回复、反应、置顶、建群 |
| **tools/qq_actions.py** | QQ 表情反应、消息置顶 |
| **tools/wecom_actions.py** | 企业微信回复、群发、上传/下载素材 |
| **tools/wedrive.py** | 企业微信微盘：空间与文件 CRUD |
| **tools/computer_use.py** | Computer Use 工具注册：computer_action（高层 GUI 任务），低层工具不再注册给主 Agent |
| **tools/computer/grounding.py** | GroundingEngine：自主 GUI 任务执行器，VisionAPIBackend 可插拔（默认 Qwen3VL） |
| **tools/computer/actions.py** | ActionBackend：PyAutoGUI + screencapture 封装（点击/输入/快捷键/滚动/截图） |
| **tools/computer/memory.py** | ActionMemory：滑动窗口截图 + 文本动作历史 + 关键快照 + 经验记录 |
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

**企业微信**：自建应用通过 HTTP 回调接收消息，需公网可访问的 Gateway（或内网穿透）。在管理后台配置「接收消息」回调 URL 为 `https://你的域名/wecom/callback`，并配置 Token、EncodingAESKey。微盘工具需在后台为应用开启「微盘」API 权限。

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
   - 执行/命令 → run_command（沙箱感知）, shell_session, sandbox
   - 浏览器 → browser(action=open/goto/snapshot/click/close)
   - 提醒/定时 → scheduler(action=add/list/cancel)
   - 记忆 → memory(action=search/add)
   - 跨渠道发消息 → send_message Tool
   - 项目管理 → DefaultAgent 加载 project_manager Skill
   - GUI 操作 → computer_action（需 pyautogui + Accessibility 权限）
   - 子任务 → agent(action=spawn/list/query/stop)
   - 切换模型 → config(action=switch_profile)
   - 热加载 Skill → config(action=reload_skills)
   - 动态 MCP → mcp(action=connect/disconnect/list)

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
| **Channels** | Telegram, Discord, Slack, Feishu, QQ, WeCom (企业微信) | ✅ |
| **Tools** | registry, channel, scheduler (合并 action-based), filesystem, shell (run_command + shell_session + sandbox，合并 action-based), web, image, browser (合并 action-based), sandbox (基础设施), mcp_client, memory (合并 action-based), wecom_actions, wedrive, computer_use (GUI 操作), subagent (合并为 agent action-based), config_manager (合并为 config action-based), mcp_tools (合并为 mcp action-based) | ✅ |
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
- [x] 多模态图片处理（图片持久化到会话历史 + 上下文恢复）
- [x] Tool result 图片路径自动检测（零侵入：_extract_image_paths → 自动构建多模态 user message）
- [x] Computer Use config_set 热重载（config_set computer_use.enabled=true 即时生效）
- [] 持久化 Shell 会话
- [] Docker 沙箱执行
- [] MCP 协议接入
- [x] 世界信息传递（channel, user_id, timestamp, is_owner）
- [x] NO_REPLY 机制
- [x] Channel Tools（channel_tools 配置自动加载）
- [] 跨渠道身份统一（Identity Mapping）
- [] 记忆分层（Memory Scope: global + personal）
- [x] Memory Tools（Agent 主动搜索/添加记忆）
- [x] Sub-Agent 系统（动态 spawn，自定义 prompt/tools/model，前台+后台模式）
- [x] 运行时配置热更新（switch_llm_profile、reload_skills、config_get/set）
- [x] MCP 动态热插拔（mcp_connect/disconnect/list）
- [] Computer Use（GUI 操作：computer_action，低层工具已移至 GroundingEngine 内部）
- [x] send_message Tool（Agent 主动跨渠道发消息）
- [x] CLI Client（WebSocket 连接 Gateway）
- [x] Unified deliver pattern（Dispatcher → channel.deliver）
- [x] WebSocket RPC（CLI Client 提供工具给 Agent）
- [x] System wake messages（周期性唤醒 + 定时任务唤醒）
- [x] Contact Registry（启动扫描 + 懒积累）
- [x] 通讯录注入 system prompt（唤醒消息时显示）

### 待测试

- [x] Browser 工具 browser(action=open/goto/click/fill/snapshot/screenshot/close)
- [ ] Discord Channel deliver 模式完整对话
- [ ] Telegram Channel deliver 模式完整对话  
- [ ] CLI Client WebSocket RPC 工具调用
- [ ] 周期性唤醒 (wake_interval > 0)
- [ ] Scheduler 唤醒 Agent 后使用 send_message 投递
- [ ] Worker 分离模式在新架构下运行
- [ ] Slack Channel 完整对话测试
- [ ] 飞书 Channel 完整对话测试
- [ ] QQ Channel 完整对话测试（频道/群/C2C）
- [ ] WeCom Channel 回调验证（/wecom/callback GET 验签 + POST 消息）
- [ ] WeCom Channel 单聊/群聊 deliver 测试
- [ ] WeDrive 微盘工具测试（需后台开启微盘 API 权限）
- [ ] Contact Registry 启动扫描验证
- [ ] 周期性唤醒通讯录可见性验证
- [ ] Computer Use: computer_action 完整 GUI 任务执行（需 pyautogui + Accessibility 权限）
- [ ] Computer Use: screenshot / gui_click / gui_type 低层工具
- [ ] Computer Use: Qwen3VL Vision API 定位精度验证
- [ ] Sub-Agent: agent_spawn 前台模式完整执行（含 tool 调用）
- [ ] Sub-Agent: agent_spawn background=true + agent_query/agent_stop 生命周期
- [ ] Sub-Agent: 使用不同 llm_profile 的子 Agent（如 deepseek_chat）
- [ ] Config: switch_llm_profile 切换后对话正常
- [ ] Config: reload_skills 修改 SKILL.md 后立即生效
- [ ] MCP: mcp_connect 连接外部 MCP Server 并发现工具
- [ ] MCP: mcp_disconnect 断开后工具不再可用

### 运行中已知问题（可选优化）

| 现象 | 说明与建议 |
|------|------------|
| LLM 120s/182s 超时 | 已支持 `config.agent.llm_call_timeout`（默认 120）。若仍超时，可适当调大或检查模型侧延迟。 |
| 同渠道多会话并发 | Slack 已按 thread_id 隔离 session；若多 thread 同时进消息会串行处理。如需严格串行可按 channel+thread 加锁（未实现）。 |
| Ctrl+C 时 posthog atexit 报错 | 本仓库未依赖 posthog；若出现多为 IDE/环境注入。可在 main 的 signal 处理里忽略 atexit 阶段的 KeyboardInterrupt（按需）。 |
| PPT 自我审查单次请求极慢（400s+） | 若一次性把多张预览图塞进一次 LLM 请求，请求体巨大，接口会极慢。Skill 已改为「只选 1～3 张关键页」做 Vision 审查；另可把 `agent.llm_call_timeout` 从 600 调低到 180～300，避免单轮等太久。 |

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-02-15 | **工具合并 + 多模态修复 + Computer Use 精简 + run_command 增强**。(1) 工具大合并：29 个工具合并为 8 个（shell_session、sandbox、agent、config、mcp、scheduler、memory、browser 各合为一个 action-based 工具），减少 tool schema 占用 token，保留全部功能。(2) 多模态图片持久化：ChatMessage 支持 images 字段，图片路径存入 SQLite，加载上下文时自动重建 OpenAI Vision 格式（base64 转换在 LLM 调用时执行）。(3) **Tool result 图片自动检测**（零侵入）：`BaseAgent._extract_image_paths` 自动扫描 tool result 文本中的图片路径，存在则构建多模态 user message 塞给 LLM 查看——工具无需改签名，只要输出含路径就行。(4) **Computer Use 精简**：移除低层工具注册（screenshot/gui_click/gui_type/gui_hotkey/gui_scroll），主 Agent 只保留 `computer_action` 高层工具，低层操作由 GroundingEngine 内部直接调用 ActionBackend。简单截图推荐 `run_command(command='screencapture -x shot.png', use_sandbox=false)`。(5) **config_set 支持 Computer Use 热重载**：修改 `computer_use.*` 配置后自动调用 `init_computer_use` 重新初始化。`init_computer_use` 在 disable 时正确清理全局状态。(6) run_command 描述增强：Agent 现在明确知道沙箱状态、容器环境（/workspace）、use_sandbox 参数控制宿主机/沙箱切换。(7) config.yaml 中 agents.default.prompt 真正生效（修复 AgentLoop 未读取 config prompt 的 bug），删除死配置 agents.study_coach。 |
| 2026-02-13 | **自适应框架演进：动态 Sub-Agent + 配置热更新 + MCP 热插拔**。(1) Sub-Agent 全面重写：主 Agent 即时定义 prompt/tools/model（不再依赖预定义 Skill），前台（阻塞）+ 后台（异步）模式，支持不同 LLM Profile，生命周期管理（agent_query/agent_stop）。(2) 运行时配置热更新：config_get/set（dot-path 读写）、switch_llm_profile（切换模型并重建 Agent）、reload_skills（重新扫描 SKILL.md 并更新 Agent）。(3) MCP 动态热插拔：mcp_connect/disconnect/list，Agent 可在对话中连接新 MCP Server 获得新能力。清理旧 run_subagent 死代码。 |
| 2026-02-10 | **Computer Use (GUI 操作)**。Hierarchical ReAct 架构：主 Agent 发出高层 `computer_action` 指令，GroundingEngine 自主完成全部 GUI 子步骤（截图→VisionLLM 规划定位→PyAutoGUI 执行→验证）。6 个工具：computer_action（高层）+ screenshot/gui_click/gui_type/gui_hotkey/gui_scroll（低层）。Vision 后端可插拔（BaseVisionBackend，当前 VisionAPIBackend 默认 Qwen3VL，切换模型只改 config）。ActionMemory 四层记忆。依赖 pyautogui + pyperclip。设计文档：docs/ui-use-design.md。 |
| 2026-02-09 | **WeCom (企业微信) Channel + WeDrive 微盘**。自建应用回调模式接入（HTTP GET/POST /wecom/callback，AES 加解密）；access_token 自动刷新；单聊/群聊收发消息 + 附件上传；wecom_actions 工具（回复/群发/素材上传下载）；wedrive 微盘工具集（空间列表/创建/重命名、文件列表/上传/下载/删除/移动/重命名）；依赖 pycryptodome。 |
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
pyautogui>=0.9.54
pyperclip>=1.8.0
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
| 动态 Prompt | 根据任务类型、用户历史动态生成 prompt（部分已实现：sub-agent 自定义 prompt） |
| 插件系统 | Channel/Tool 作为独立包动态加载（部分已实现：MCP 动态热插拔） |
| Web 前端 | 管理界面 + 对话 UI |
| Computer Use 增强 | ShowUI 本地模型、Set-of-Mark 标注、macOS Accessibility、经验学习 |
| 泛化多模态 | `_extract_image_paths` → `_extract_media_paths`（支持 video/audio）；LLM Profile 增加 `modalities` 字段按模型能力过滤；`_build_user_message` 根据 media type 构建对应 content block（image_url / input_audio 等）；Gemini 原生 API 适配（inline_data parts） |

### 长期

| 方向 | 说明 |
|------|------|
| Multi-Agent | 多 Agent 协作（Planner → Coder → Reviewer）（基础已实现：动态 spawn_agent） |
| 分布式部署 | Gateway 云端 + Agent 本地 |
| 图记忆 | Knowledge Graph 增强记忆系统 |
