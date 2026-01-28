# Personal Agent Hub

一个可扩展的个人 AI 助手框架，支持多渠道接入、多 Agent 人设、可插拔 Tools、长期记忆。

## 快速开始

### 1. 创建 Conda 环境

```bash
# 创建新环境
conda create -n agent-hub python=3.10 -y

# 激活环境
conda activate agent-hub

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 设置火山引擎 API Key（必需）
export ARK_API_KEY="your-ark-api-key-here"

# 如果使用 Telegram，还需要设置（可选）
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
```

### 3. 运行

**CLI 模式（推荐先测试）**：
```bash
python main.py start
```

**单次对话测试**：
```bash
python main.py chat "你好"
python main.py chat "我想学习 Python" --agent study_coach
```

## 配置说明

配置文件 `config.yaml`：

```yaml
# LLM 配置（火山引擎方舟）
llm:
  api_key: ${ARK_API_KEY}  # 从环境变量读取
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model: ep-20260128095801-jc4gx  # 火山引擎模型端点

# 数据目录
data:
  dir: ./data

# Channel 配置
channels:
  cli:
    enabled: true   # CLI 模式
  telegram:
    enabled: false  # Telegram 需要设置 token
    token: ${TELEGRAM_BOT_TOKEN}
    allowed_users: ["your-telegram-user-id"]

# 路由规则
routing:
  - match: {pattern: "学习|复习|督促"}
    agent: study_coach
    tools: [scheduler_add, scheduler_list]
  - match: {}  # 兜底
    agent: default
    tools: []
```

## 项目结构

```
personal_agent_hub/
├── main.py                 # CLI 入口
├── config.yaml             # 配置文件
├── core/
│   ├── engine.py           # 主引擎
│   ├── router.py           # 消息路由
│   └── types.py            # 共享类型
├── channels/
│   ├── base.py             # Channel 基类
│   ├── cli.py              # CLI 交互
│   └── telegram.py         # Telegram Bot
├── agents/
│   ├── base.py             # Agent 基类
│   └── study_coach.py      # 学习教练
├── tools/
│   ├── registry.py         # Tool 注册
│   └── scheduler.py        # 定时提醒
├── memory/
│   ├── session.py          # 对话历史 (SQLite)
│   ├── global_mem.py       # 长期记忆 (ChromaDB)
│   └── manager.py          # Memory 管理
└── data/                   # 数据目录（自动创建）
    ├── sessions.db         # SQLite 数据库
    └── chroma/             # ChromaDB 向量库
```

## 扩展开发

### 添加新 Tool（让 Agent 能操作电脑/调用 API 等）

**步骤**：
1. 在 `tools/` 目录创建新文件
2. 使用 `@registry.register` 装饰器注册
3. 在 `core/engine.py` 中 import 该文件（触发装饰器）
4. 在 `config.yaml` 的路由规则中添加 tool 名称

**示例：文件系统操作 Tool**

```python
# tools/filesystem.py
from tools.registry import registry
import os
import subprocess

@registry.register(
    name="create_folder",
    description="创建文件夹",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件夹路径"}
        },
        "required": ["path"]
    }
)
async def create_folder(path: str, context=None) -> str:
    os.makedirs(path, exist_ok=True)
    return f"已创建文件夹: {path}"

@registry.register(
    name="run_command",
    description="执行 shell 命令",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"}
        },
        "required": ["command"]
    }
)
async def run_command(command: str, context=None) -> str:
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout or result.stderr or "命令执行完成"
```

**在 engine.py 中注册**：
```python
# core/engine.py 顶部添加
import tools.filesystem  # 触发装饰器注册
```

**在 config.yaml 中启用**：
```yaml
routing:
  - match: {pattern: "创建|文件|命令|执行"}
    agent: default
    tools: [create_folder, run_command]
```

---

### 添加新 Agent

**步骤**：
1. 在 `agents/` 目录创建新文件，继承 `BaseAgent`
2. 在 `core/engine.py` 的 `_init_agents()` 中初始化
3. 在 `config.yaml` 的路由规则中使用

```python
# agents/coder.py
from agents.base import BaseAgent

class CoderAgent(BaseAgent):
    DEFAULT_PROMPT = """你是一个编程助手，帮助用户写代码、调试问题。
你可以使用工具来创建文件、执行命令。"""
    
    def __init__(self, llm_config: dict, custom_prompt: str = None):
        prompt = custom_prompt or self.DEFAULT_PROMPT
        super().__init__("coder", prompt, llm_config)
```

---

### 添加新 Channel

**步骤**：
1. 在 `channels/` 目录创建新文件，继承 `BaseChannel`
2. 实现 `start()`, `send()`, `stop()` 方法
3. 在 `core/engine.py` 的 `_init_channels()` 中初始化
4. 在 `config.yaml` 中添加配置

```python
# channels/webhook.py
from channels.base import BaseChannel, MessageHandler
from core.types import IncomingMessage, OutgoingMessage
import aiohttp

class WebhookChannel(BaseChannel):
    """Webhook Channel - 接收/发送 HTTP 请求"""
    
    def __init__(self, webhook_url: str, on_message: MessageHandler):
        super().__init__(on_message)
        self.webhook_url = webhook_url
    
    async def start(self):
        # 启动 HTTP 服务器监听
        pass
    
    async def send(self, user_id: str, message: OutgoingMessage):
        async with aiohttp.ClientSession() as session:
            await session.post(self.webhook_url, json={"text": message.text})
    
    async def stop(self):
        pass
```

---

### 定时任务

**方式 1：通过对话让 Agent 设置**
```
You: 每天早上 9 点提醒我学习
Agent: 好的，已设置提醒（调用 scheduler_add tool）
```

**方式 2：系统启动时自动添加**
```python
# 在 engine.py 的 run() 方法中添加
async def run(self):
    self._init_channels()
    self._init_agents()
    self.scheduler.start()
    
    # 添加启动时的定时任务
    self.scheduler.add_job(
        self._daily_greeting,
        'cron',
        hour=9,
        minute=0
    )
    
    # ...

async def _daily_greeting(self):
    await self.send_push("cli", "cli_user", "早上好！今天要学习什么？")
```

---

## 系统架构

```
用户消息
    │
    ▼
Channel (CLI/Telegram/...)
    │
    ▼ IncomingMessage
Engine.handle()
    ├── Router.resolve() ──────────► 选择 Agent + 允许的 Tools
    ├── MemoryManager.get_context() ► 获取历史 + 相关记忆 (ChromaDB)
    ├── Agent.run() ───────────────► 调用 LLM + 执行 Tool
    │       │
    │       ├── LLM 决定调用 Tool
    │       ├── registry.execute() ► 执行 Tool（注入 context）
    │       └── LLM 生成最终回复
    │
    └── MemoryManager.save() ──────► 保存对话 (SQLite)
    │
    ▼ OutgoingMessage
Channel.send() ► 返回给用户
```

---

## 关键文件

| 文件 | 职责 | 修改场景 |
|------|------|----------|
| `config.yaml` | 配置 LLM、路由、Agent prompt | 调整模型、添加路由规则 |
| `core/engine.py` | 主引擎，组装所有组件 | 添加新 Channel/Agent 初始化 |
| `core/router.py` | 消息路由 | 修改路由逻辑 |
| `tools/registry.py` | Tool 注册系统 | 一般不需要修改 |
| `tools/*.py` | 具体 Tool 实现 | 添加新能力 |
| `agents/base.py` | Agent 基类，LLM 调用 | 修改 LLM 调用逻辑 |
| `agents/*.py` | 具体 Agent 实现 | 添加新人设 |
| `channels/base.py` | Channel 基类 | 一般不需要修改 |
| `channels/*.py` | 具体 Channel 实现 | 添加新渠道 |
| `memory/manager.py` | 记忆管理 | 修改记忆策略 |

## License

MIT
