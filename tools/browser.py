"""
Browser Tools - 无头浏览器（Playwright）

供 Agent 执行需要 JS 渲染、点击、填表、截图等操作。
使用前需安装: pip install playwright && playwright install chromium

跨平台：macOS/Windows 一般可直接用；Linux（尤其 Docker/无桌面）常需先装 Chromium 系统依赖，
见 docs/browser-tool-research.md「系统与平台」。
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# 单例：当前进程内共用一个 browser 会话
_browser = None
_context = None
_page = None
_playwright = None

DEFAULT_TIMEOUT_MS = 30000  # 30 秒
SNAPSHOT_MAX_CHARS = 15000  # 快照文本最大长度，避免 token 爆炸
SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "screenshots")


def _get_page():
    """获取当前 page，未打开则返回 None"""
    global _page
    return _page


async def _ensure_page():
    """若未打开则先 open"""
    if _get_page() is not None:
        return True
    # 未打开时由调用方提示先 browser_open
    return False


@registry.register(
    name="browser",
    description=(
        "Control a headless Chromium browser for web browsing, form filling, and page interaction. "
        "Actions: open (start browser), goto (navigate to URL), click (click element), fill (fill form input), "
        "snapshot (get page text content), screenshot (capture page image), close (release browser). "
        "Typical flow: open -> goto -> snapshot/screenshot -> click/fill -> snapshot -> close."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "goto", "click", "fill", "snapshot", "screenshot", "close"],
                "description": "Action to perform"
            },
            "url": {"type": "string", "description": "URL to navigate to (for goto)"},
            "selector": {"type": "string", "description": "CSS selector or text=... locator (for click/fill/screenshot)"},
            "value": {"type": "string", "description": "Text to fill (for fill)"}
        },
        "required": ["action"]
    }
)
async def browser(action: str, url: str = None, selector: str = None, value: str = None, context=None) -> str:
    """Control a headless Chromium browser."""

    if action == "open":
        return await _browser_open()
    elif action == "goto":
        if not url:
            return "错误: goto 操作需要 url"
        return await _browser_goto(url)
    elif action == "click":
        if not selector:
            return "错误: click 操作需要 selector"
        return await _browser_click(selector)
    elif action == "fill":
        if not selector:
            return "错误: fill 操作需要 selector"
        if value is None:
            return "错误: fill 操作需要 value"
        return await _browser_fill(selector, value)
    elif action == "snapshot":
        return await _browser_snapshot()
    elif action == "screenshot":
        return await _browser_screenshot(selector)
    elif action == "close":
        return await _browser_close()
    else:
        return f"错误: 未知 action '{action}'。可用: open, goto, click, fill, snapshot, screenshot, close"


async def _browser_open() -> str:
    """启动无头浏览器。若已打开则直接返回成功。"""
    global _browser, _context, _page, _playwright  # noqa: PLW0603
    if _page is not None:
        return "浏览器已处于打开状态，可直接使用 browser(action='goto') 等。"
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return "错误: 未安装 playwright。请执行: pip install playwright && playwright install chromium"
    try:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
        _context = await _browser.new_context()
        _context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        _page = await _context.new_page()
        return "浏览器已启动（无头模式）。可使用 goto、click、fill、snapshot、screenshot，用完后 close。"
    except Exception as e:
        logger.error(f"browser open failed: {e}", exc_info=True)
        return f"启动浏览器失败: {str(e)}。请确认已执行: playwright install chromium"


async def _browser_goto(url: str) -> str:
    """打开 URL"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser(action='open')。"
    try:
        await _page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        title = await _page.title()
        return f"已打开: {_page.url}\n标题: {title}"
    except Exception as e:
        logger.error(f"browser goto failed: {e}", exc_info=True)
        return f"打开页面失败: {str(e)}"


async def _browser_click(selector: str) -> str:
    """点击元素"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser(action='open')。"
    try:
        await _page.click(selector, timeout=DEFAULT_TIMEOUT_MS)
        return f"已点击: {selector}"
    except Exception as e:
        logger.error(f"browser click failed: {e}", exc_info=True)
        return f"点击失败: {str(e)}"


async def _browser_fill(selector: str, value: str) -> str:
    """填表"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser(action='open')。"
    try:
        await _page.fill(selector, value, timeout=DEFAULT_TIMEOUT_MS)
        return f"已在 {selector} 填入内容（共 {len(value)} 字）"
    except Exception as e:
        logger.error(f"browser fill failed: {e}", exc_info=True)
        return f"填表失败: {str(e)}"


async def _browser_snapshot() -> str:
    """获取当前页文本快照"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser(action='open')。"
    try:
        title = await _page.title()
        url = _page.url
        body = await _page.evaluate("""() => {
            const el = document.body;
            if (!el) return '';
            return el.innerText || el.textContent || '';
        }""")
        if len(body) > SNAPSHOT_MAX_CHARS:
            body = body[:SNAPSHOT_MAX_CHARS] + "\n...[已截断]"
        return f"URL: {url}\n标题: {title}\n\n--- 页面文本 ---\n{body}"
    except Exception as e:
        logger.error(f"browser snapshot failed: {e}", exc_info=True)
        return f"获取快照失败: {str(e)}"


async def _browser_screenshot(selector: Optional[str] = None) -> str:
    """对当前页面或指定元素截图，保存到 data/screenshots/，返回路径。"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser(action='open')。"
    try:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{ts}.png"
        path = os.path.join(SCREENSHOTS_DIR, filename)
        if selector:
            el = await _page.wait_for_selector(selector, timeout=DEFAULT_TIMEOUT_MS)
            await el.screenshot(path=path)
        else:
            await _page.screenshot(path=path, full_page=True)
        abs_path = os.path.abspath(path)
        return f"截图已保存: {abs_path}\n（若需让模型看图，可将此路径作为图片输入。）"
    except Exception as e:
        logger.error(f"browser screenshot failed: {e}", exc_info=True)
        return f"截图失败: {str(e)}"


async def _browser_close() -> str:
    """关闭浏览器"""
    global _browser, _context, _page, _playwright  # noqa: PLW0603
    if _page is None:
        return "浏览器未打开，无需关闭。"
    try:
        if _context:
            await _context.close()
            _context = None
        if _browser:
            await _browser.close()
            _browser = None
        if _playwright:
            await _playwright.stop()
            _playwright = None
        _page = None
        return "浏览器已关闭。"
    except Exception as e:
        logger.error(f"browser close failed: {e}", exc_info=True)
        _page = None
        _context = None
        _browser = None
        _playwright = None
        return f"关闭时出错: {str(e)}，已清理状态。"
