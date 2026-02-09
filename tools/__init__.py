# 导入所有 tool 模块以注册工具
from tools import filesystem
from tools import scheduler
from tools import shell      # 含 run_command, shell_session_*, sandbox_stop/status/copy_*
from tools import web
from tools import image
from tools import mcp_client
from tools import discord_actions
# from tools import subagent  # 已禁用：需迁移到 MessageBus 架构，见 subagent.py TODO
from tools import memory
from tools import channel
from tools import sandbox     # 仅导入基础设施（DockerSandbox 类），工具注册在 shell.py
import tools.slack_actions
import tools.feishu_actions
import tools.qq_actions
