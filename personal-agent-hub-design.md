# Personal Agent Hub - 系统设计文档

> 版本: 1.0 | 更新: 2026-01-27

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
│   ├── cli.py              # CLI Channel（Phase 0 开发用）
│   └── telegram.py         # Telegram 实现
├── agents/
│   ├── base.py             # Agent 基类
│   └── study_coach.py      # 学习教练 Agent
├── tools/
│   ├── registry.py         # Tool 注册与执行（依赖注入）
│   └── scheduler.py        # 定时任务 Tool
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
        ├── channels/telegram.py ── channels/base.py
        ├── agents/study_coach.py ── agents/base.py
        ├── tools/registry.py
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

### 3.10 `tools/scheduler.py` - 定时任务 Tool

**职责**: 实现定时提醒功能

**依赖**: `tools/registry.py`, `APScheduler` 库

**Tools 定义**（使用依赖注入，无全局变量）:
```python
from tools.registry import registry

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
            }
        },
        "required": ["time", "content", "user_id", "channel"]
    }
)
async def scheduler_add(time: str, content: str, user_id: str, channel: str, context=None) -> str:
    """
    添加定时任务（使用注入的 context）
    
    context 包含:
    - engine: Engine 实例（用于 send_push）
    - scheduler: APScheduler 实例
    
    返回: "已设置提醒：2026-01-28 10:00 - 复习 GRPO"
    """
    engine = context["engine"]
    scheduler = context["scheduler"]
    
    async def job_callback():
        await engine.send_push(channel, user_id, f"⏰ 提醒: {content}")
    
    # scheduler.add_job(job_callback, 'date', run_date=parse_time(time))
    pass

@registry.register(
    name="scheduler_list",
    description="列出用户的所有定时提醒",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string"}
        },
        "required": ["user_id"]
    }
)
async def scheduler_list(user_id: str, context=None) -> str:
    """列出定时任务"""
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
    """取消任务"""
    pass
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
