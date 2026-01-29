# Personal Agent Hub - 开发追踪

> 最后更新: 2026-01-29

快速了解项目架构和开发进展。详细技术设计见 `personal-agent-hub-design.md`。

---

## 项目简介

**Personal Agent Hub** 是一个可扩展的个人 AI 助手框架：
- 多渠道接入（CLI / Telegram / Discord）
- 多 Agent 人设（学习教练、通用助手...）
- 可插拔 Tools（定时提醒、文件操作、网页搜索...）
- 长期记忆（Session 历史 + RAG 向量搜索）

---

## 系统架构

### 目录结构

```
personal_agent_hub/
├── main.py              # CLI 入口
├── config.yaml          # 配置文件
├── core/
│   ├── engine.py        # 主引擎（消息调度中心）
│   ├── router.py        # 消息路由（规则匹配）
│   └── types.py         # 共享类型定义
├── channels/
│   ├── base.py          # Channel 抽象基类
│   ├── cli.py           # CLI 交互
│   ├── telegram.py      # Telegram Bot
│   └── discord.py       # Discord Bot
├── agents/
│   ├── base.py          # Agent 基类（LLM 调用 + Tool 执行）
│   └── study_coach.py   # 学习教练 Agent
├── tools/
│   ├── registry.py      # Tool 注册系统（装饰器 + 依赖注入）
│   ├── scheduler.py     # 智能定时提醒（支持自动续约）
│   ├── filesystem.py    # 文件操作
│   ├── shell.py         # Shell 命令执行
│   └── web.py           # 网页搜索 / 抓取
├── memory/
│   ├── session.py       # 对话历史（SQLite）
│   ├── global_mem.py    # 长期记忆（ChromaDB 向量）
│   └── manager.py       # Memory 统一入口
└── data/                # 运行时数据
    ├── sessions.db
    └── chroma/
```

### 消息流

```
Channel (CLI/Telegram/Discord)
    │
    ▼ IncomingMessage
Engine.handle()
    ├── Router.resolve() → 选择 Agent + Tools
    ├── MemoryManager.get_context() → 历史 + 记忆
    ├── Agent.run() → LLM 处理 + Tool 调用
    └── MemoryManager.save() → 保存对话
    │
    ▼ OutgoingMessage
Channel.send() → 用户
```

### 依赖关系

```
main.py
  └── core/engine.py
        ├── core/router.py
        ├── channels/* ── channels/base.py
        ├── agents/* ── agents/base.py
        ├── tools/registry.py ← (各 tool 模块注册)
        └── memory/manager.py
              ├── memory/session.py
              └── memory/global_mem.py
```

---

## 模块说明

| 模块 | 职责 |
|------|------|
| **core/engine.py** | 主引擎，组装所有组件，处理消息流转，提供主动推送 |
| **core/router.py** | 根据规则（关键词/正则）决定消息由哪个 Agent 处理 |
| **core/types.py** | 共享数据结构：IncomingMessage, OutgoingMessage, Route 等 |
| **channels/base.py** | Channel 抽象接口：start(), send(), stop() |
| **channels/cli.py** | 命令行交互，开发调试用 |
| **channels/telegram.py** | Telegram Bot，支持白名单 |
| **channels/discord.py** | Discord Bot，支持白名单 |
| **agents/base.py** | Agent 基类，实现 LLM 调用循环 + Tool 执行 |
| **agents/study_coach.py** | 学习教练人设（严厉督促 + 提醒） |
| **tools/registry.py** | Tool 注册装饰器，支持依赖注入 context |
| **tools/scheduler.py** | 定时提醒，支持 `auto_continue` 智能续约 |
| **tools/filesystem.py** | 文件 CRUD：create, read, list, append, delete, send |
| **tools/shell.py** | 执行 shell 命令 |
| **tools/web.py** | web_search（搜索）、fetch_url（抓取网页） |
| **memory/session.py** | SQLite 存储完整对话历史 |
| **memory/global_mem.py** | ChromaDB 向量搜索长期记忆 |
| **memory/manager.py** | 统一入口，结合 Session + GlobalMemory |

---

## 扩展指南

### 添加新 Tool

1. 在 `tools/` 下创建文件
2. 使用 `@registry.register()` 装饰器注册
3. 在 `config.yaml` 路由规则中添加 tool 名称

```python
from tools.registry import registry

@registry.register(
    name="my_tool",
    description="工具描述",
    parameters={...}
)
async def my_tool(arg1: str, context=None) -> str:
    engine = context["engine"]  # 可选：依赖注入
    return "结果"
```

### 添加新 Agent

1. 在 `agents/` 下创建文件，继承 `BaseAgent`
2. 定义 `DEFAULT_PROMPT`
3. 在 `config.yaml` 的 `agents` 中配置

### 添加新 Channel

1. 在 `channels/` 下创建文件，继承 `BaseChannel`
2. 实现 `start()`, `send()`, `stop()`
3. 在 `config.yaml` 的 `channels` 中配置

---

## 当前配置

### LLM

```yaml
llm:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: ep-20260128095801-jc4gx  # 火山引擎
```

### 路由规则

| 匹配关键词 | Agent | Tools |
|-----------|-------|-------|
| 学习/复习/督促 | study_coach | scheduler_add, scheduler_list |
| 搜索/查找/网页... | default | web_search, fetch_url, create_file, send_file |
| 执行/运行/命令... | default | run_command |
| 创建/写入/文件... | default | filesystem 相关 |
| 提醒/定时/闹钟 | default | scheduler 相关 |
| 兜底 | default | 全部 tools |

---

## 开发状态

### 已完成模块

| 类别 | 模块 | 状态 |
|------|------|------|
| **Core** | engine, router, types | ✅ |
| **Channels** | CLI, Telegram, Discord | ✅ |
| **Agents** | base, study_coach | ✅ |
| **Tools** | registry, scheduler, filesystem, shell, web | ✅ |
| **Memory** | session, global_mem, manager | ✅ |

### 已验证功能

- [x] CLI 模式对话
- [x] 火山引擎 API 调用（OpenAI 兼容）
- [x] 消息路由（关键词匹配）
- [x] Tool 注册与执行（依赖注入）
- [x] 对话历史保存（SQLite）
- [x] 向量记忆搜索（ChromaDB）
- [x] 智能定时提醒（自动续约 + Agent 决策下次提醒）
- [x] Discord Bot 集成

---

## 更新日志

| 日期 | 更新内容 |
|------|----------|
| 2026-01-29 | Scheduler 智能化：`auto_continue` 参数，触发时唤醒 Agent 自动设置下次提醒 |
| 2026-01-29 | 新增 Discord Channel |
| 2026-01-28 | 初始版本，完成 Phase 0 核心功能（CLI 对话、Tool 调用、Memory） |

---

## 未来方向

### 短期

| 方向 | 说明 |
|------|------|
| 智能路由 | 用 LLM (Meta-Agent) 动态决定路由，替代关键词匹配 |
| 更多 Tools | calendar（日程）、email、notion 集成 |
| 微信 Channel | 个人微信 / 企业微信接入 |

### 中期

| 方向 | 说明 |
|------|------|
| MCP 协议 | 支持 Model Context Protocol，接入社区工具生态 |
| 动态 Prompt | 根据任务类型、用户历史动态生成 Agent prompt |
| Web Channel | HTTP API + 前端界面 |

### 长期

| 方向 | 说明 |
|------|------|
| Multi-Agent | 多 Agent 协作（Planner → Coder → Reviewer） |
| Skill 系统 | 可导入/导出的 Agent 配置包 |
