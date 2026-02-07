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
    import signal
    from gateway.app import Gateway
    gateway = Gateway(config)

    async def _run():
        loop = asyncio.get_event_loop()
        shutdown_triggered = asyncio.Event()

        def _signal_handler():
            if not shutdown_triggered.is_set():
                shutdown_triggered.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        # Run gateway in background task so we can watch for signal
        run_task = asyncio.create_task(gateway.run())

        # Wait for either the run task to finish or a signal
        done = asyncio.Event()

        async def _watch_signal():
            await shutdown_triggered.wait()
            logging.getLogger(__name__).info("Shutdown signal received")
            await gateway.shutdown()
            done.set()

        async def _watch_task():
            await run_task
            done.set()

        asyncio.create_task(_watch_signal())
        asyncio.create_task(_watch_task())
        await done.wait()

    asyncio.run(_run())


@app.command()
def client(
    host: str = typer.Option("localhost", "--host", "-h", help="Gateway host"),
    port: int = typer.Option(8080, "--port", "-p", help="Gateway port"),
    api_key: str = typer.Option(None, "--api-key", "-k", help="API key"),
    user_id: str = typer.Option("cli_user", "--user-id", "-u", help="User ID"),
    max_turns: int = typer.Option(0, "--max-turns", "-t", help="Max conversation turns (0 = unlimited)"),
):
    """启动 CLI Client（通过 WebSocket 连接 Gateway）"""
    from cli_client.client import CLIClient
    cli = CLIClient(host=host, port=port, api_key=api_key, user_id=user_id, max_turns=max_turns)
    asyncio.run(cli.run())


if __name__ == "__main__":
    app()
