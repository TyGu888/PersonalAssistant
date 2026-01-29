# Personal Agent Hub - 系统设计文档

> 版本: 1.1 | 更新: 2026-01-29

---

## 项目简介

**Personal Agent Hub** 是一个可扩展的个人 AI 助手框架，支持：
- 多渠道接入（Telegram、微信、Web）
- 多 Agent 人设（学习教练、生活助手...）
- 可插拔 Tools（定时提醒、写文档、上网搜索...）
- 长期记忆（Session + RAG）

### 核心理念

| 理念 | 说明 |
|------|------|
| **Gateway 中心化** | 所有消息经 Engine 统一调度 |
| **Channel 可插拔** | 新增渠道只需实现 BaseChannel |
| **Tool 装饰器注册** | `@tool` 装饰器自动注册 |
| **Memory 分层** | Session（完整历史）→ GlobalMemory（精炼记忆） |

### 消息流

```
Channel (Telegram/微信/Web)
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

### 扩展方式

| 扩展类型 | 方法 |
|----------|------|
| 新增渠道 | 实现 `BaseChannel`，加到 `channels/` |
| 新增 Agent | 继承 `BaseAgent`，加到 `agents/` |
| 新增 Tool | 用 `@tool` 装饰器，加到 `tools/` |

---

## 文档结构

| 章节 | 内容 |
|------|------|
| 1. 系统概览 | 目录结构、依赖关系 |
| 2. 共享类型 | `core/types.py` 数据结构 |
| 3. 各文件设计 | 每个文件的职责、接口、输入输出 |
| 4. 开发顺序 | 并行开发建议 |
| 5. 依赖库 | requirements.txt |
| 6. 环境变量 | 必需配置 |

---

## 1. 系统概览

```
personal_agent_hub/
├── main.py                 # CLI 入口
├── config.yaml             # 配置文件
├── core/
│   ├── engine.py           # 主引擎
│   ├── router.py           # 消息路由
│   └── types.py            # 共享类型定义
├── channels/
│   ├── base.py             # Channel 基类
│   ├── cli.py              # CLI Channel（开发调试用）
│   ├── telegram.py         # Telegram Bot
│   └── discord.py          # Discord Bot
├── agents/
│   ├── base.py             # Agent 基类
│   └── study_coach.py      # 学习教练 Agent
├── tools/
│   ├── registry.py         # Tool 注册与执行（依赖注入）
│   ├── scheduler.py        # 智能定时任务 Tool
│   ├── filesystem.py       # 文件操作 Tool
│   ├── shell.py            # Shell 命令 Tool
│   └── web.py              # 网页搜索/抓取 Tool
├── memory/
│   ├── session.py          # Session 存储（SQLite）
│   ├── global_mem.py       # 全局记忆（ChromaDB）
│   └── manager.py          # Memory 统一入口
└── data/                   # 数据目录（自动创建）
    ├── sessions.db         # SQLite 数据库
    └── chroma/             # ChromaDB 向量库
```

**依赖关系图**:
```
main.py
  └── core/engine.py
        ├── core/router.py
        ├── channels/
        │     ├── cli.py ─────────┐
        │     ├── telegram.py ────┼── channels/base.py
        │     └── discord.py ─────┘
        ├── agents/study_coach.py ── agents/base.py
        ├── tools/
        │     ├── registry.py
        │     ├── scheduler.py ── (依赖注入 engine, scheduler)
        │     ├── filesystem.py
        │     ├── shell.py
        │     └── web.py
        └── memory/manager.py
              ├── memory/session.py
              └── memory/global_mem.py
```

---

## 2. 共享类型定义

### `core/types.py`

所有模块共享的数据结构，**必须首先开发**。

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any
from enum import Enum

# ===== Channel 相关 =====

@dataclass
class IncomingMessage:
    """输入消息（所有 Channel 统一格式）"""
    channel: str              # "telegram" | "wechat" | "cli"
    user_id: str              # 用户唯一标识
    text: str                 # 消息文本
    is_group: bool = False    # 是否群聊
    group_id: Optional[str] = None  # 群 ID（群聊时必填）
    timestamp: datetime = field(default_factory=datetime.utcnow)
    attachments: list = field(default_factory=list)  # 附件路径
    raw: dict = field(default_factory=dict)          # 原始数据
    
    def get_session_id(self) -> str:
        """生成 Session Key（标准化）"""
        if self.is_group:
            return f"{self.channel}:group:{self.group_id}:user:{self.user_id}"
        return f"{self.channel}:dm:{self.user_id}"

@dataclass
class OutgoingMessage:
    """输出消息"""
    text: str
    attachments: list = field(default_factory=list)

# ===== Router 相关 =====

@dataclass
class Route:
    """路由结果"""
    agent_id: str             # 目标 Agent ID
    tools: list[str]          # 允许使用的 Tool 列表

# ===== Memory 相关 =====

@dataclass
class ChatMessage:
    """单条对话消息"""
    role: str                 # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass
class MemoryItem:
    """一条长期记忆"""
    id: str
    user_id: str
    type: str                 # "preference" | "fact" | "event" | "commitment"
    content: str
    embedding: list[float]
    source_session: str
    created_at: datetime
    active: bool = True

# ===== Tool 相关 =====

@dataclass
class ToolResult:
    """Tool 执行结果"""
    success: bool
    output: str
    error: Optional[str] = None
```

---

## 3. 各文件详细设计

---

### 3.1 `main.py` - CLI 入口

**职责**: 解析命令行参数，启动 Engine

**依赖**: `core/engine.py`

**命令**:
```bash
# 启动服务
python main.py start --config config.yaml

# 单次对话（测试用）
python main.py chat "你好" --agent study_coach
```

**接口**:
```python
def start(config: str = "config.yaml"):
    """启动 Agent Hub 服务"""

def chat(message: str, agent: str = "default"):
    """发送单条消息并打印回复"""
```

---

### 3.2 `config.yaml` - 配置文件

**格式**:
```yaml
# LLM 配置
openai:
  api_key: ${OPENAI_API_KEY}  # 从环境变量读取
  model: gpt-4o

# 数据目录（SQLite + ChromaDB）
data:
  dir: ./data  # 会创建 sessions.db 和 chroma/

# Channel 配置
channels:
  cli:
    enabled: true   # Phase 0 开发用
  telegram:
    enabled: false  # Phase 0 先关闭
    token: ${TELEGRAM_BOT_TOKEN}
    allowed_users: ["123456789"]  # 白名单

# 路由规则（按顺序匹配，第一个匹配生效）
routing:
  - match: {pattern: "学习|复习|督促"}
    agent: study_coach
    tools: [scheduler_add, scheduler_list]
  
  - match: {}  # 兜底
    agent: default
    tools: []

# Agent 定义
agents:
  study_coach:
    prompt: |
      你是一个严厉但关心学生的学习教练。
      督促用户学习，提醒完成任务。
  
  default:
    prompt: |
      你是一个友好的助手。
```

---

### 3.3 `core/engine.py` - 主引擎

**职责**: 组装所有组件，处理消息流转，提供主动推送能力

**依赖**: `router.py`, `channels/*`, `agents/*`, `memory/manager.py`, `tools/registry.py`

**类定义**:
```python
class Engine:
    def __init__(self, config_path: str):
        """
        初始化:
        1. 加载配置
        2. 初始化 Router
        3. 初始化 MemoryManager
        4. 初始化 ToolRegistry（注入 self 作为 context）
        5. 初始化 channels: dict[str, BaseChannel]  # ← 维护 Channel 实例
        6. 懒加载 Agents
        """
        self.channels: dict[str, BaseChannel] = {}  # "telegram" -> TelegramChannel
        self.agents: dict[str, BaseAgent] = {}
    
    async def run(self):
        """
        启动服务:
        1. 启动所有 enabled 的 Channels
        2. 阻塞等待
        """
        pass
    
    async def send_push(self, channel: str, user_id: str, text: str):
        """
        主动推送消息（供 Scheduler 等 Tool 调用）
        
        输入:
        - channel: Channel 名称 ("telegram", "wechat")
        - user_id: 用户 ID
        - text: 推送内容
        """
        if channel in self.channels:
            await self.channels[channel].send(user_id, OutgoingMessage(text=text))
    
    def get_tool_context(self) -> dict:
        """
        获取 Tool 执行时需要的上下文（依赖注入）
        """
        return {
            "engine": self,
            "scheduler": self.scheduler,
            "memory": self.memory
        }
    
    async def handle(self, msg: IncomingMessage) -> OutgoingMessage:
        """
        处理消息（核心流程）:
        
        输入: IncomingMessage
        输出: OutgoingMessage
        
        流程:
        1. session_id = msg.get_session_id()  # ← 标准化 Session Key
        2. Router.resolve(msg) -> Route
        3. 获取/创建 Agent 实例
        4. MemoryManager.get_context(session_id, msg.text) -> 历史 + 相关记忆
        5. tools = registry.get_schemas(route.tools)
        6. response = Agent.run(msg.text, context, tools, self.get_tool_context())
        7. MemoryManager.save(session_id, msg, response)
        8. return OutgoingMessage(text=response)
        """
        pass
    
    async def handle_cli(self, text: str, agent_id: str) -> str:
        """CLI 模式处理（测试用，Phase 0）"""
        pass
```

---

### 3.4 `core/router.py` - 消息路由

**职责**: 根据规则决定消息由哪个 Agent 处理

**依赖**: `core/types.py`

**类定义**:
```python
class Router:
    def __init__(self, rules: list[dict]):
        """
        rules 格式:
        [
            {
                "match": {"channel": "telegram", "pattern": "学习.*"},
                "agent": "study_coach",
                "tools": ["scheduler"]
            }
        ]
        """
        pass
    
    def resolve(self, msg: IncomingMessage) -> Route:
        """
        输入: IncomingMessage
        输出: Route(agent_id, tools)
        
        匹配逻辑:
        1. 遍历 rules
        2. 检查 channel 是否匹配
        3. 检查 user_id 是否匹配（如果指定）
        4. 检查 text 是否匹配 pattern（正则）
        5. 全部通过则返回该 Route
        6. 无匹配则返回默认 Route("default", [])
        """
        pass
```

---

### 3.5 `channels/base.py` - Channel 基类

**职责**: 定义 Channel 接口

**类定义**:
```python
from abc import ABC, abstractmethod
from typing import Callable, Awaitable
from core.types import IncomingMessage, OutgoingMessage

MessageHandler = Callable[[IncomingMessage], Awaitable[OutgoingMessage]]

class BaseChannel(ABC):
    def __init__(self, on_message: MessageHandler):
        """
        on_message: 消息处理回调（由 Engine 传入）
        """
        self.on_message = on_message
    
    @abstractmethod
    async def start(self):
        """启动 Channel（阻塞）"""
        pass
    
    @abstractmethod
    async def send(self, user_id: str, message: OutgoingMessage):
        """主动发送消息（用于定时提醒）"""
        pass
    
    @abstractmethod
    async def stop(self):
        """停止 Channel"""
        pass
```

---

### 3.6 `channels/telegram.py` - Telegram 实现

**职责**: 实现 Telegram Bot

**依赖**: `channels/base.py`, `python-telegram-bot` 库

**类定义**:
```python
from channels.base import BaseChannel, MessageHandler
from core.types import IncomingMessage, OutgoingMessage

class TelegramChannel(BaseChannel):
    def __init__(self, token: str, allowed_users: list[str], on_message: MessageHandler):
        """
        token: Telegram Bot Token
        allowed_users: 允许的用户 ID 列表（白名单）
        """
        pass
    
    async def start(self):
        """
        启动 Telegram Bot:
        1. 创建 Application
        2. 注册 message handler
        3. 启动 polling
        """
        pass
    
    async def _handle_telegram_message(self, update, context):
        """
        Telegram 消息处理:
        1. 检查用户是否在白名单
        2. 转换为 IncomingMessage
        3. 调用 self.on_message
        4. 发送回复
        """
        pass
    
    async def send(self, user_id: str, message: OutgoingMessage):
        """主动发消息"""
        pass
    
    async def stop(self):
        """停止 Bot"""
        pass
```

---

### 3.7 `agents/base.py` - Agent 基类

**职责**: 定义 Agent 接口，实现 LLM 调用和 Tool 执行逻辑

**依赖**: `core/types.py`, `tools/registry.py`, `openai` 库

**类定义**:
```python
from openai import AsyncOpenAI

class BaseAgent:
    def __init__(self, agent_id: str, system_prompt: str, openai_config: dict):
        """
        agent_id: Agent 标识
        system_prompt: 系统提示词
        openai_config: {"api_key": "...", "model": "gpt-4o"}
        """
        pass
    
    async def run(self, user_text: str, context: dict, tools: list[dict]) -> str:
        """
        输入:
        - user_text: 用户消息
        - context: {
            "history": list[ChatMessage],  # 最近对话历史
            "memories": list[str]          # 相关长期记忆
          }
        - tools: Tool schema 列表（OpenAI 格式）
        
        输出: str（回复文本）
        
        流程:
        1. 构建 messages:
           - system: prompt + memories
           - history: 最近对话
           - user: 当前消息
        2. 调用 LLM
        3. 如果有 tool_calls:
           a. 执行 tools
           b. 将结果追加到 messages
           c. 重新调用 LLM
           d. 循环直到无 tool_calls
        4. 返回最终回复
        """
        pass
    
    async def _execute_tool(self, tool_call) -> str:
        """执行单个 Tool"""
        pass
```

---

### 3.8 `agents/study_coach.py` - 学习教练

**职责**: 实现学习教练人设

**依赖**: `agents/base.py`

**类定义**:
```python
from agents.base import BaseAgent

class StudyCoachAgent(BaseAgent):
    DEFAULT_PROMPT = """你是一个严厉但关心学生的学习教练。
你的职责是督促用户学习，提醒他们完成任务，并在他们懈怠时给予警告。
语气要直接、坚定，但也要有适度的鼓励。

你可以使用的工具:
- scheduler: 设置定时提醒

当用户说要学习某个东西时，主动问是否需要设置提醒。"""
    
    def __init__(self, openai_config: dict, custom_prompt: str = None):
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("study_coach", prompt, openai_config)
```

---

### 3.9 `tools/registry.py` - Tool 注册与执行（依赖注入）

**职责**: 管理所有 Tools，提供注册和执行接口，支持上下文注入

**依赖**: `core/types.py`

**设计要点**:
- 使用类实例而非全局变量
- Tool 函数可声明 `context` 参数，自动注入 Engine/Scheduler 等

**接口**:
```python
from typing import Callable
from core.types import ToolResult
import inspect

class ToolRegistry:
    def __init__(self):
        self._tools: dict = {}
    
    def register(self, name: str, description: str, parameters: dict):
        """
        装饰器: 注册一个 Tool
        
        使用示例:
        @registry.register(
            name="scheduler_add",
            description="添加定时提醒",
            parameters={
                "type": "object",
                "properties": {
                    "time": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["time", "content"]
            }
        )
        async def scheduler_add(time: str, content: str, context=None) -> str:
            # context 由 Engine 在调用时自动注入
            engine = context["engine"]
            await engine.send_push(...)
        """
        pass
    
    def get_schemas(self, names: list[str]) -> list[dict]:
        """
        输入: Tool 名称列表 ["scheduler_add", "scheduler_list"]
        输出: OpenAI Tool 格式的 schema 列表
        [
            {
                "type": "function",
                "function": {
                    "name": "scheduler_add",
                    "description": "添加定时提醒",
                    "parameters": {...}
                }
        }
    ]
    """
    pass
    
    async def execute(self, name: str, args_dict: dict, context: dict = None) -> ToolResult:
        """
        执行 Tool（支持依赖注入）
        
        输入:
        - name: Tool 名称
        - args_dict: 参数字典（已解析的 JSON）
        - context: 注入的上下文 {"engine": ..., "scheduler": ..., "memory": ...}
        
        输出: ToolResult(success, output, error)
        
        流程:
        1. 检查 Tool 是否存在
        2. 检查 Tool 函数是否有 context 参数
        3. 如有，则注入 context
        4. 执行并返回结果
        """
        pass

# 全局单例
registry = ToolRegistry()
```

---

### 3.10 `tools/scheduler.py` - 智能定时任务 Tool

**职责**: 实现智能定时提醒功能，支持自动续约

**依赖**: `tools/registry.py`, `APScheduler` 库, `dateutil` 库

**核心特性**:
- **单次提醒**: 触发时直接推送消息
- **智能循环提醒** (`auto_continue=True`): 触发时唤醒 Agent 处理，Agent 可决定下次提醒时间

**工作原理**:
```
单次提醒 (auto_continue=False)
    │
    ▼ 时间到
直接推送 "⏰ 提醒: {content}"

智能循环提醒 (auto_continue=True)
    │
    ▼ 时间到
构造 IncomingMessage → Engine.handle()
    │
    ▼
Agent 处理并决定:
    1. 回复用户（提醒内容）
    2. 设置下一次提醒（可选，自动续约）
    3. 设置追问提醒（可选）
```

**关键实现细节**:

1. **时间解析** (`parse_reminder_time`):
   - 支持 `HH:MM` 格式（今天，如已过则自动设为明天）
   - 支持 `YYYY-MM-DD HH:MM` 完整格式
   - 使用 `dateutil.parser` 作为通用解析后备

2. **Job ID 格式**: `reminder_{user_id}_{uuid8}`（便于按用户过滤）

3. **错误处理**:
   - context 为空检查
   - 时间解析失败处理
   - 过去时间检测并返回错误

**Tools 定义**:
```python
from tools.registry import registry
from datetime import datetime, timedelta
from dateutil import parser as date_parser
import uuid
from core.types import IncomingMessage

@registry.register(
    name="scheduler_add",
    description="添加定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "time": {
                "type": "string",
                "description": "提醒时间。格式: 'HH:MM'(今天) 或 'YYYY-MM-DD HH:MM'"
            },
            "content": {
                "type": "string", 
                "description": "提醒内容"
            },
            "user_id": {
                "type": "string",
                "description": "提醒谁（用户ID）"
            },
            "channel": {
                "type": "string",
                "description": "通过哪个渠道提醒"
            },
            "auto_continue": {
                "type": "boolean",
                "description": "是否为循环提醒。若为 True，触发时会唤醒 Agent 处理，Agent 可决定是否设置下一次提醒"
            }
        },
        "required": ["time", "content", "user_id", "channel"]
    }
)
async def scheduler_add(time: str, content: str, user_id: str, channel: str, 
                        auto_continue: bool = False, context=None) -> str:
    """
    添加定时任务
    
    流程:
    1. 验证 context（engine, scheduler）
    2. 解析时间，检查是否为未来时间
    3. 生成唯一 job_id
    4. 创建 job_callback（根据 auto_continue 决定行为）
    5. 添加到 scheduler
    
    返回: "已设置循环提醒：2026-01-28 10:00 - 复习 GRPO" 或
          "已设置单次提醒：2026-01-28 10:00 - 复习 GRPO"
    """
    # 验证 context
    if context is None:
        return "错误: 缺少上下文信息"
    engine = context.get("engine")
    scheduler = context.get("scheduler")
    
    # 解析时间 & 检查是否过期
    run_date = parse_reminder_time(time)
    if run_date <= datetime.now():
        return f"错误: 提醒时间已过去，请设置未来时间"
    
    job_id = f"reminder_{user_id}_{uuid.uuid4().hex[:8]}"
    
    async def job_callback(content=None, user_id=None, channel=None, auto_continue=False):
        try:
            if auto_continue:
                # 智能循环：构造系统消息，让 Agent 处理并决定下次提醒
                system_msg = IncomingMessage(
                    channel=channel,
                    user_id=user_id,
                    text=f"[定时任务触发] 内容：{content}。请提醒用户，并根据情况决定是否设置下一次提醒（使用 scheduler_add，记得设置 auto_continue=True）。"
                )
                response = await engine.handle(system_msg)
                await engine.send_push(channel, user_id, response.text)
            else:
                # 简单推送
                await engine.send_push(channel, user_id, f"⏰ 提醒: {content}")
        except Exception as e:
            print(f"提醒发送失败: {e}")
    
    scheduler.add_job(
        job_callback,
        'date',
        run_date=run_date,
        id=job_id,
        kwargs={'content': content, 'user_id': user_id, 'channel': channel, 'auto_continue': auto_continue},
        replace_existing=True
    )
    
    mode = "循环提醒" if auto_continue else "单次提醒"
    return f"已设置{mode}：{run_date.strftime('%Y-%m-%d %H:%M')} - {content}"

@registry.register(
    name="scheduler_list",
    description="列出用户的所有定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "用户ID"}
        },
        "required": ["user_id"]
    }
)
async def scheduler_list(user_id: str, context=None) -> str:
    """
    列出定时任务（通过 job_id 前缀过滤用户任务）
    
    输出格式:
    用户 {user_id} 的定时提醒列表：
    1. [abc123] [循环] 2026-01-29 10:00 - 复习 GRPO
    2. [def456] [单次] 2026-01-29 15:00 - 开会
    """
    pass

@registry.register(
    name="scheduler_cancel",
    description="取消定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "任务ID"}
        },
        "required": ["job_id"]
    }
)
async def scheduler_cancel(job_id: str, context=None) -> str:
    """取消任务，返回 "已取消提醒: {job_id}" """
    pass
```

**Agent Prompt 配置示例**（用于处理 auto_continue 触发）:
```yaml
agents:
  study_coach:
    prompt: |
      你是一个严厉但关心学生的学习教练。
      
      ## 处理定时任务触发
      当收到 [定时任务触发] 开头的消息时：
      1. 友好地提醒用户
      2. 设置下一次提醒（scheduler_add，auto_continue=True）
      3. 可选：设置1小时后的追问提醒（auto_continue=False）
```

---

### 3.11 `memory/session.py` - Session 存储

**职责**: 存储完整对话历史

**依赖**: `core/types.py`, `sqlite3`（内置）

**数据库选型**: SQLite（单文件，零运维，适合个人项目）

**类定义**:
```python
from core.types import ChatMessage
import sqlite3
import json

class SessionStore:
    def __init__(self, db_path: str = "data/sessions.db"):
        """
        初始化 SQLite 连接
        
        表结构:
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,          -- session_id
            messages TEXT,                -- JSON array
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
        pass
    
    def append(self, session_id: str, message: ChatMessage):
        """
        追加一条消息到 Session
        
        流程:
        1. 读取现有 messages (JSON)
        2. 追加新消息
        3. 更新 updated_at
        """
        pass
    
    def get_recent(self, session_id: str, n: int = 20) -> list[ChatMessage]:
        """
        获取最近 n 条消息
        
        输出: [ChatMessage, ...]
        """
        pass
    
    def get_all(self, session_id: str) -> list[ChatMessage]:
        """获取全部历史（用于记忆提取）"""
        pass
    
    def clear(self, session_id: str):
        """清空 Session"""
        pass
```

---

### 3.12 `memory/global_mem.py` - 全局记忆

**职责**: 存储精炼后的长期记忆，支持向量搜索

**依赖**: `core/types.py`, `chromadb` 库, `openai` 库（embedding）

**数据库选型**: ChromaDB（本地向量数据库，零运维）

**类定义**:
```python
from core.types import MemoryItem
import chromadb

class GlobalMemory:
    def __init__(self, db_path: str = "data/chroma", openai_api_key: str = None):
        """
        初始化 ChromaDB
        初始化 OpenAI client（用于 embedding，可选，ChromaDB 有内置 embedding）
        
        ChromaDB Collection:
        - name: "memories"
        - metadata: {"user_id", "type", "source_session", "created_at", "active"}
        """
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection("memories")
    
    async def add(self, user_id: str, content: str, memory_type: str, source_session: str):
        """
        添加一条记忆
        
        流程:
        1. 生成唯一 ID
        2. ChromaDB 自动计算 embedding（或用 OpenAI）
        3. 存入 collection
        """
        pass
    
    async def search(self, user_id: str, query: str, top_k: int = 5) -> list[MemoryItem]:
        """
        向量相似度搜索
        
        输入:
        - user_id: 只搜索该用户的记忆
        - query: 查询文本
        - top_k: 返回数量
        
        输出: [MemoryItem, ...] 按相似度排序
        """
        pass
    
    def deactivate(self, memory_id: str):
        """标记记忆为过期（软删除）"""
        pass
```

---

### 3.13 `memory/manager.py` - Memory 统一入口

**职责**: 统一管理 Session 和 GlobalMemory，提供记忆提取功能

**依赖**: `memory/session.py`, `memory/global_mem.py`, `openai` 库

**类定义**:
```python
class MemoryManager:
    def __init__(self, data_dir: str = "data", openai_api_key: str = None):
        """
        初始化 SessionStore (SQLite) 和 GlobalMemory (ChromaDB)
        
        data_dir 下会创建:
        - sessions.db  (SQLite)
        - chroma/      (ChromaDB)
        """
        self.session = SessionStore(f"{data_dir}/sessions.db")
        self.global_mem = GlobalMemory(f"{data_dir}/chroma", openai_api_key)
    
    # ===== Session 操作 =====
    
    def save_message(self, session_id: str, role: str, content: str):
        """保存消息到 Session"""
        pass
    
    def get_context(self, session_id: str, query: str, history_limit: int = 20, memory_limit: int = 5) -> dict:
        """
        获取对话上下文
        
        输入:
        - session_id: Session ID
        - query: 当前用户消息（用于 RAG 搜索）
        - history_limit: 历史消息数量限制
        - memory_limit: 相关记忆数量限制
        
        输出:
        {
            "history": [ChatMessage, ...],
            "memories": ["记忆1", "记忆2", ...]
        }
        """
        pass
    
    # ===== 记忆提取 =====
    
    async def extract_memories(self, session_id: str, user_id: str) -> list[str]:
        """
        从 Session 提取记忆到 GlobalMemory
        
        流程:
        1. 获取 Session 完整历史
        2. 调用 LLM 提取关键信息
        3. 存入 GlobalMemory
        
        输出: 提取的记忆内容列表
        """
        pass
```

---

## 4. 开发顺序建议

**Phase 0: Hello World（CLI 先行，调通核心链路）**

| 目标 | 说明 |
|------|------|
| CLI 一问一答 | 在终端里输入消息，Agent 回复，不需要 Telegram |
| 调通 Tool 调用 | LLM 决定调用 Tool → 执行 → 结果回传 |
| 调通 Memory | Session 保存/读取 |

这样可以在终端里把最复杂的链路调通，比对着 Telegram 调试容易得多。

---

**Phase 1: 基础设施（可并行）**
| 文件 | 依赖 | 开发者 |
|------|------|--------|
| `core/types.py` | 无 | Agent A |
| `config.yaml` | 无 | Agent A |
| `tools/registry.py` | `types.py` | Agent B |
| `memory/session.py` | `types.py` | Agent C |

**Phase 2: 核心模块（可并行）**
| 文件 | 依赖 | 开发者 |
|------|------|--------|
| `core/router.py` | `types.py` | Agent A |
| `channels/base.py` | `types.py` | Agent B |
| `agents/base.py` | `types.py`, `tools/registry.py` | Agent C |
| `memory/global_mem.py` | `types.py` | Agent D |

**Phase 3: 具体实现（可并行）**
| 文件 | 依赖 | 开发者 |
|------|------|--------|
| `channels/cli.py` | `channels/base.py` | Agent A (Phase 0 用) |
| `channels/telegram.py` | `channels/base.py` | Agent A |
| `agents/study_coach.py` | `agents/base.py` | Agent B |
| `tools/scheduler.py` | `tools/registry.py` | Agent C |
| `memory/manager.py` | `session.py`, `global_mem.py` | Agent D |

**Phase 4: 组装**
| 文件 | 依赖 | 开发者 |
|------|------|--------|
| `core/engine.py` | 所有模块 | Agent A |
| `main.py` | `engine.py` | Agent A |

---

## 5. 依赖库

```
# requirements.txt
typer>=0.9.0
python-telegram-bot>=20.0
openai>=1.0.0
chromadb>=0.4.0          # 本地向量数据库（替代 MongoDB）
apscheduler>=3.10.0
pyyaml>=6.0
# SQLite 是 Python 内置，无需安装
```

---

## 6. 环境变量

```bash
export OPENAI_API_KEY="sk-..."
export TELEGRAM_BOT_TOKEN="123456:ABC..."  # Phase 0 不需要
```

---

## 7. 数据目录结构

```
data/
├── sessions.db     # SQLite: 对话历史
└── chroma/         # ChromaDB: 向量记忆
```

---

## 8. 当前开发状态（2026-01-29）

### 已完成功能

| 模块 | 状态 | 说明 |
|------|------|------|
| `core/types.py` | ✅ 完成 | 共享类型定义 |
| `core/router.py` | ✅ 完成 | 基于规则的消息路由 |
| `core/engine.py` | ✅ 完成 | 主引擎，组装所有组件 |
| `main.py` | ✅ 完成 | CLI 入口（typer） |
| `config.yaml` | ✅ 完成 | 配置文件，支持环境变量 |
| `channels/base.py` | ✅ 完成 | Channel 抽象基类 |
| `channels/cli.py` | ✅ 完成 | CLI 交互 Channel |
| `channels/telegram.py` | ✅ 完成 | Telegram Bot Channel |
| `channels/discord.py` | ✅ 完成 | Discord Bot Channel |
| `agents/base.py` | ✅ 完成 | Agent 基类，LLM 调用 + Tool 执行 |
| `agents/study_coach.py` | ✅ 完成 | 学习教练 + 默认 Agent |
| `tools/registry.py` | ✅ 完成 | Tool 注册系统（装饰器 + 依赖注入） |
| `tools/scheduler.py` | ✅ 完成 | 智能定时提醒 Tool（APScheduler + 自动续约） |
| `memory/session.py` | ✅ 完成 | 对话历史存储（SQLite） |
| `memory/global_mem.py` | ✅ 完成 | 长期记忆（ChromaDB 向量搜索） |
| `memory/manager.py` | ✅ 完成 | Memory 统一入口 + 记忆提取 |

### 已验证功能

- [x] CLI 模式对话
- [x] 火山引擎 API 调用（OpenAI 兼容）
- [x] 消息路由（关键词匹配）
- [x] Tool 注册与执行（依赖注入）
- [x] 对话历史保存（SQLite）
- [x] 向量记忆搜索（ChromaDB）
- [x] 智能定时提醒（自动续约 + Agent 决策下次提醒）

### 更新日志

| 日期 | 版本 | 更新内容 |
|------|------|----------|
| 2026-01-29 | 1.1 | Scheduler 智能化：新增 `auto_continue` 参数，支持循环提醒自动唤醒 Agent 决定下次提醒时间；新增 Discord Channel |
| 2026-01-28 | 1.0 | 初始版本，完成 Phase 0 核心功能 |

### 当前 LLM 配置

使用字节火山引擎方舟平台（OpenAI 兼容 API）：

```yaml
llm:
  api_key: ${ARK_API_KEY}
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: ep-20260128095801-jc4gx
```

### 当前路由规则

```yaml
routing:
  - match: {pattern: "学习|复习|督促"}
    agent: study_coach
    tools: [scheduler_add, scheduler_list]
  
  - match: {pattern: "搜索|查找|研究|调查|了解|访问|网页|抓取"}
    agent: default
    tools: [web_search, fetch_url, create_file, send_file]
  
  - match: {pattern: "执行|运行|命令|shell|git|npm|pip"}
    agent: default
    tools: [run_command]
  
  - match: {pattern: "创建|写入|读取|文件|目录|删除|追加|发送"}
    agent: default
    tools: [create_file, read_file, list_files, append_file, delete_file, send_file]
  
  - match: {pattern: "提醒|定时|闹钟|计划"}
    agent: default
    tools: [scheduler_add, scheduler_list, scheduler_cancel]
  
  - match: {}  # 兜底
    agent: default
    tools: [web_search, run_command, create_file, read_file, list_files, send_file, scheduler_add, scheduler_list, scheduler_cancel]
```

---

## 9. 未来开发方向

### 9.1 智能路由（Meta-Agent）

**现状**：基于关键词/正则的规则匹配

**目标**：用 LLM 动态决定路由

```
用户任务
    │
    ▼
┌─────────────────────────────────────┐
│ Meta-Agent (Orchestrator)           │
│ - 分析任务意图和复杂度               │
│ - 决定使用哪个 Agent                │
│ - 动态生成/调整 prompt              │
│ - 决定分配哪些 tools                │
└─────────────────────────────────────┘
    │
    ▼
执行 Agent（动态配置）
```

**实现思路**：
1. 创建 `OrchestratorAgent`
2. 给它一个描述所有 Agent 能力的 prompt
3. 让它输出 JSON 决定路由
4. 在 `Router` 中增加 LLM 路由模式

---

### 9.2 动态 Prompt 生成

**现状**：Agent 的 prompt 是静态配置的

**目标**：根据任务类型、用户历史、当前上下文动态生成 prompt

```python
class DynamicPromptAgent(BaseAgent):
    async def _generate_prompt(self, task_type: str, user_context: dict) -> str:
        # 用 LLM 生成针对性 prompt
        pass
```

---

### 9.3 MCP 协议支持

**现状**：自定义装饰器注册 Tool

**目标**：支持 MCP (Model Context Protocol)，接入社区工具生态

| 对比 | 当前实现 | MCP |
|------|----------|-----|
| 协议 | 自定义装饰器 | 标准化 JSON-RPC |
| 工具发现 | 代码里写死 | 动态发现 |
| 跨进程 | 不支持 | 支持（Server/Client） |
| 生态 | 自己造轮子 | 可复用社区工具 |

**实现思路**：
1. 添加 `MCPClient` 类
2. 在 `ToolRegistry` 中支持从 MCP Server 动态加载工具
3. 兼容现有的装饰器注册方式

```python
# tools/mcp_client.py
class MCPClient:
    async def discover_tools(self, server_url: str) -> list[dict]:
        """从 MCP Server 获取可用工具列表"""
        pass
    
    async def call_tool(self, server_url: str, tool_name: str, args: dict) -> str:
        """调用 MCP Server 上的工具"""
        pass
```

---

### 9.4 Agent 链（Multi-Agent 协作）

**现状**：单个 Agent 处理任务

**目标**：多个 Agent 协作完成复杂任务

```
用户: "帮我写一个 Python 爬虫并部署到服务器"
         │
         ▼
    ┌─────────────┐
    │ Planner     │ → 分解任务
    └─────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌───────┐
│ Coder │ │DevOps │ → 各自完成子任务
└───────┘ └───────┘
    │         │
    └────┬────┘
         ▼
    ┌─────────────┐
    │ Reviewer    │ → 检查整合
    └─────────────┘
```

---

### 9.5 Skill 系统（可导入/导出的 Agent 配置）

**现状**：Agent 配置写在代码和 config.yaml 中

**目标**：支持 Skill 文件格式，可分享、导入

```yaml
# skills/python_tutor.skill.yaml
name: Python 教程助手
version: 1.0
author: xxx

agent:
  id: python_tutor
  prompt: |
    你是一个 Python 编程导师...

tools:
  - run_command
  - create_file
  - read_file

routing:
  pattern: "Python|编程|代码"
```

---

### 9.6 更多 Tool 开发

| Tool | 说明 | 状态 |
|------|------|--------|
| `scheduler` | 智能定时提醒（支持自动续约） | ✅ 已完成 |
| `filesystem` | 文件/文件夹操作 | ✅ 已完成 |
| `shell` | 执行 shell 命令 | ✅ 已完成 |
| `web_search` | 网页搜索 | ✅ 已完成 |
| `fetch_url` | 抓取网页内容 | ✅ 已完成 |
| `calendar` | 日历/日程管理 | 待开发 |
| `email` | 邮件发送 | 待开发 |
| `notion` | Notion API | 待开发 |

---

### 9.7 更多 Channel 开发

| Channel | 说明 | 状态 |
|---------|------|--------|
| CLI | 命令行交互 | ✅ 已完成 |
| Telegram | Telegram Bot | ✅ 已完成 |
| Discord | Discord Bot | ✅ 已完成 |
| 微信 | 个人微信/企业微信 | 待开发 |
| Web | HTTP API + 前端界面 | 待开发 |
| Slack | Slack Bot | 待开发 |

---

## 10. 开发指南

详见 `README.md`，包含：
- 环境配置
- 添加新 Tool 的步骤
- 添加新 Agent 的步骤
- 添加新 Channel 的步骤
- 系统架构图
