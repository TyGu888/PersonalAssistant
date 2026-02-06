import typer
import asyncio
import logging
from dotenv import load_dotenv

# 自动加载 .env
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)

app = typer.Typer(help="Personal Agent Hub - Agent-Centric Architecture")


@app.command()
def start(
    config: str = typer.Option("config.yaml", "--config", "-c", help="配置文件路径")
):
    """启动 Gateway 服务（包含 Agent、Channels、FastAPI）"""
    from gateway.app import Gateway
    gateway = Gateway(config)
    asyncio.run(gateway.run())


@app.command()
def chat(
    message: str = typer.Argument(..., help="要发送的消息"),
    config: str = typer.Option("config.yaml", "--config", "-c", help="配置文件路径")
):
    """发送单条消息并打印回复（测试用）"""
    from gateway.app import Gateway
    gateway = Gateway(config)
    response = asyncio.run(gateway.handle_cli(message))
    print(f"Response: {response}")


@app.command()
def client(
    host: str = typer.Option("localhost", "--host", "-h", help="Gateway host"),
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port"),
    api_key: str = typer.Option(None, "--api-key", "-k", help="API key"),
    user_id: str = typer.Option("cli_user", "--user-id", "-u", help="User ID"),
):
    """启动 CLI Client（通过 WebSocket 连接 Gateway）"""
    from cli_client.client import CLIClient
    cli = CLIClient(host=host, port=port, api_key=api_key, user_id=user_id)
    asyncio.run(cli.run())


if __name__ == "__main__":
    app()
