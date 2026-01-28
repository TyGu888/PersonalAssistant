import typer
import asyncio
import logging
from dotenv import load_dotenv
from core.engine import Engine

# 自动加载 .env 文件中的环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)

app = typer.Typer(help="Personal Agent Hub - 个人 AI 助手框架")


@app.command()
def start(
    config: str = typer.Option("config.yaml", "--config", "-c", help="配置文件路径")
):
    """启动 Agent Hub 服务"""
    engine = Engine(config)
    asyncio.run(engine.run())


@app.command()
def chat(
    message: str = typer.Argument(..., help="要发送的消息"),
    agent: str = typer.Option("default", "--agent", "-a", help="使用的 Agent"),
    config: str = typer.Option("config.yaml", "--config", "-c", help="配置文件路径")
):
    """发送单条消息并打印回复（测试用）"""
    engine = Engine(config)
    # 需要先初始化 agents
    engine._init_agents()
    response = asyncio.run(engine.handle_cli(message, agent))
    print(f"Response: {response}")


if __name__ == "__main__":
    app()
