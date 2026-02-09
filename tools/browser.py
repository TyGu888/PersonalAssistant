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
    name="browser_open",
    description="启动无头浏览器（Chromium）。后续可用 browser_goto / browser_click / browser_snapshot / browser_screenshot 等。用完后请 browser_close。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def browser_open(context=None) -> str:
    """启动无头浏览器。若已打开则直接返回成功。"""
    global _browser, _context, _page, _playwright  # noqa: PLW0603
    if _page is not None:
        return "浏览器已处于打开状态，可直接使用 browser_goto / browser_click 等。"
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
        return "浏览器已启动（无头模式）。可使用 browser_goto、browser_click、browser_fill、browser_snapshot、browser_screenshot，用完后 browser_close。"
    except Exception as e:
        logger.error(f"browser_open failed: {e}", exc_info=True)
        return f"启动浏览器失败: {str(e)}。请确认已执行: playwright install chromium"


@registry.register(
    name="browser_goto",
    description="在已打开的浏览器中打开指定 URL。需先 browser_open。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的完整 URL，如 https://example.com"}
        },
        "required": ["url"],
    },
)
async def browser_goto(url: str, context=None) -> str:
    """打开 URL"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser_open。"
    try:
        await _page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        title = await _page.title()
        return f"已打开: {_page.url}\n标题: {title}"
    except Exception as e:
        logger.error(f"browser_goto failed: {e}", exc_info=True)
        return f"打开页面失败: {str(e)}"


@registry.register(
    name="browser_click",
    description="点击页面上的元素。selector 可为 CSS 选择器、或 text=按钮文字、或 [aria-label=...]。需先 browser_open 并已 browser_goto 到目标页。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "元素选择器，如 'button'、'#submit'、'text=登录'"}
        },
        "required": ["selector"],
    },
)
async def browser_click(selector: str, context=None) -> str:
    """点击元素"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser_open。"
    try:
        await _page.click(selector, timeout=DEFAULT_TIMEOUT_MS)
        return f"已点击: {selector}"
    except Exception as e:
        logger.error(f"browser_click failed: {e}", exc_info=True)
        return f"点击失败: {str(e)}"


@registry.register(
    name="browser_fill",
    description="在输入框内填入文字。selector 通常为 input 的 name、id 或 CSS 选择器。需先 browser_open 并已 browser_goto 到目标页。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "输入框选择器，如 'input[name=q]'、'#search'"},
            "value": {"type": "string", "description": "要填入的文字"}
        },
        "required": ["selector", "value"],
    },
)
async def browser_fill(selector: str, value: str, context=None) -> str:
    """填表"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser_open。"
    try:
        await _page.fill(selector, value, timeout=DEFAULT_TIMEOUT_MS)
        return f"已在 {selector} 填入内容（共 {len(value)} 字）"
    except Exception as e:
        logger.error(f"browser_fill failed: {e}", exc_info=True)
        return f"填表失败: {str(e)}"


@registry.register(
    name="browser_snapshot",
    description="获取当前页面的文本快照（URL、标题、body 主要文本），供理解页面内容。内容过长会截断。需先 browser_open 并已 browser_goto。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def browser_snapshot(context=None) -> str:
    """获取当前页文本快照"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser_open。"
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
        logger.error(f"browser_snapshot failed: {e}", exc_info=True)
        return f"获取快照失败: {str(e)}"


@registry.register(
    name="browser_screenshot",
    description="对当前页面截图（整页或指定元素），保存为 PNG，返回文件路径。用于“看”页面视觉内容。需先 browser_open 并已 browser_goto。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "可选。不传则截整页；传则截该元素（CSS 选择器）。"
            }
        },
        "required": [],
    },
)
async def browser_screenshot(selector: Optional[str] = None, context=None) -> str:
    """对当前页面或指定元素截图，保存到 data/screenshots/，返回路径。"""
    if not await _ensure_page():
        return "错误: 浏览器未打开，请先调用 browser_open。"
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
        logger.error(f"browser_screenshot failed: {e}", exc_info=True)
        return f"截图失败: {str(e)}"


@registry.register(
    name="browser_close",
    description="关闭无头浏览器并释放资源。用完后应调用以免占用内存。",
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def browser_close(context=None) -> str:
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
        logger.error(f"browser_close failed: {e}", exc_info=True)
        _page = None
        _context = None
        _browser = None
        _playwright = None
        return f"关闭时出错: {str(e)}，已清理状态。"
