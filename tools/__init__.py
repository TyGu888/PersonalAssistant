# 导入所有 tool 模块以注册工具
from tools import filesystem
from tools import scheduler
from tools import shell      # 含 run_command, shell_session_*, sandbox_stop/status/copy_*
from tools import web
from tools import image
from tools import mcp_client
from tools import discord_actions
from tools import subagent
from tools import memory
from tools import channel
from tools import sandbox     # 仅导入基础设施（DockerSandbox 类），工具注册在 shell.py
import tools.slack_actions
import tools.feishu_actions
import tools.qq_actions
import tools.wecom_actions
import tools.wedrive
import tools.computer_use    # Computer Use (GUI 操作), 需要 pyautogui
import tools.config_manager  # 运行时配置热更新
import tools.mcp_tools       # MCP 动态热插拔
