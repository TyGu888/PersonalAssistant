"""
Action Backend - 系统级输入操作封装

macOS: screencapture + PyAutoGUI
Linux: PyAutoGUI
中文输入: pyperclip + 粘贴

注意:
- macOS 需要辅助功能权限: System Settings → Privacy → Accessibility
- Retina 屏幕: PyAutoGUI 使用逻辑坐标（非物理像素）
"""

import os
import subprocess
import platform
import logging

logger = logging.getLogger(__name__)


class ActionBackend:
    """系统级鼠标键盘操作 + 截图"""

    def __init__(self, screenshot_dir: str = "/tmp/agent_screenshots"):
        self.platform = platform.system()  # "Darwin", "Linux", "Windows"
        self.screenshot_dir = screenshot_dir
        self._screenshot_counter = 0
        os.makedirs(self.screenshot_dir, exist_ok=True)

    def screenshot(self, region: dict = None) -> str:
        """
        截图，返回文件路径。
        macOS 优先用 screencapture（更快、更可靠）。
        """
        self._screenshot_counter += 1
        filename = f"screen_{self._screenshot_counter:04d}.png"
        path = os.path.join(self.screenshot_dir, filename)

        if self.platform == "Darwin":
            cmd = ["screencapture", "-x"]  # -x = 无声
            if region:
                cmd.extend(["-R", f"{region['x']},{region['y']},{region['width']},{region['height']}"])
            cmd.append(path)
            subprocess.run(cmd, check=True)
        else:
            import pyautogui
            img = pyautogui.screenshot(region=tuple(region.values()) if region else None)
            img.save(path)

        return path

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1):
        import pyautogui
        pyautogui.click(x, y, button=button, clicks=clicks)

    def type_text(self, text: str):
        """
        输入文本。
        非 ASCII（中文等）通过剪贴板粘贴。
        """
        import pyautogui

        if all(ord(c) < 128 for c in text):
            pyautogui.typewrite(text, interval=0.02)
        else:
            import pyperclip
            pyperclip.copy(text)
            mod = "command" if self.platform == "Darwin" else "ctrl"
            pyautogui.hotkey(mod, "v")

    def hotkey(self, *keys: str):
        import pyautogui
        normalized = []
        for k in keys:
            k = k.lower().strip()
            if k == "cmd":
                k = "command" if self.platform == "Darwin" else "ctrl"
            normalized.append(k)
        pyautogui.hotkey(*normalized)

    def scroll(self, direction: str, amount: int = 3):
        import pyautogui
        if direction == "up":
            pyautogui.scroll(amount)
        elif direction == "down":
            pyautogui.scroll(-amount)
        elif direction == "left":
            pyautogui.hscroll(-amount)
        elif direction == "right":
            pyautogui.hscroll(amount)

    def mouse_move(self, x: int, y: int):
        import pyautogui
        pyautogui.moveTo(x, y)
