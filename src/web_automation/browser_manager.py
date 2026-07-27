"""
浏览器管理器 - 无痕模式启动，不保存任何 Cookie 和缓存
"""
import asyncio
import os
import signal
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from src.utils.config import Settings


class BrowserManager:
    """管理 Stealth 浏览器实例"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._context = None
        self._page = None
        self._browser = None
        self._playwright = None
        self._window_size = self._detect_window_size()

    @staticmethod
    def _detect_window_size() -> tuple[int, int]:
        """检测屏幕可用尺寸，返回合适的窗口大小，留出任务栏边距"""
        # 兜底默认值
        default = (1400, 900)
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # GetSystemMetrics(0)=SM_CXSCREEN, GetSystemMetrics(1)=SM_CYSCREEN
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            if sw <= 0 or sh <= 0:
                return default
            # 窗口全屏时留出边距（任务栏约 50px）
            w = sw - 80
            h = sh - 100
            # 上限保护（4K 屏幕不要开太大）
            w = min(w, 1600)
            h = min(h, 1000)
            # 下限保护
            w = max(w, 1024)
            h = max(h, 700)
            return (w, h)
        except Exception:
            return default

    def _storage_path(self) -> Path:
        """（保留兼容性，无痕模式不使用）"""
        return Path(tempfile.gettempdir()) / "patent_tool_noop.json"

    async def launch(self) -> Tuple:
        """启动浏览器（Edge 无痕模式）"""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        headless = self.settings.web_headless
        proxy = self.settings.web_proxy

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
        if headless:
            launch_args.append("--headless=new")

        self._browser = await self._playwright.chromium.launch(
            channel="msedge",
            headless=headless,
            args=launch_args,
        )

        ctx_opts = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": self.settings.web_locale,
            "timezone_id": self.settings.web_timezone,
        }
        if proxy:
            ctx_opts["proxy"] = {"server": proxy}

        self._context = await self._browser.new_context(**ctx_opts)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(30000)
        self._page.on("dialog", lambda d: d.accept() if d.type != "prompt" else d.accept(""))

        return self._context, self._page

    def _kill_chrome(self):
        """兼容旧代码，CDP 模式不需要杀进程"""
        pass

    async def save_storage(self):
        pass

    async def close(self):
        """关闭浏览器"""
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

    async def launch_with_retry(self, max_retries: int = 2) -> Tuple:
        """带重试的启动"""
        for attempt in range(max_retries + 1):
            try:
                return await self.launch()
            except Exception as e:
                if attempt < max_retries:
                    print(f"启动失败(第{attempt+1}次), 重试... {e}")
                    if self._playwright:
                        try:
                            await self._playwright.stop()
                        except Exception:
                            pass
                    self._playwright = None
                    self._context = None
                    self._page = None
                    self._kill_chrome()
                    await asyncio.sleep(2)
                else:
                    raise
