"""
浏览器管理器 - 单例模式，全程共用一个 Edge 浏览器。

使用 Playwright 直接启动系统安装的 Edge（channel="msedge"），
而非 CDP 连接，确保 browser.close() 能真正关闭浏览器窗口。
"""
import asyncio
from pathlib import Path
from typing import Tuple

from src.utils.config import Settings


class BrowserManager:
    """浏览器单例管理器。Playwright 直接管理 Edge 生命周期。"""

    _instance = None
    _playwright = None
    _browser = None
    _context = None

    def __new__(cls, settings: Settings = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, settings: Settings = None):
        if self._initialized:
            return
        self.settings = settings
        self._initialized = True

    # ================================================================
    # 公开 API
    # ================================================================

    async def launch(self) -> Tuple:
        """获取浏览器 context 和 page。首次启动 Edge，后续开新标签页。"""
        if BrowserManager._browser is not None:
            try:
                page = await BrowserManager._context.new_page()
                page.set_default_timeout(60000)
                return BrowserManager._context, page
            except Exception:
                await self._cleanup()
                await asyncio.sleep(1)

        await self._start_browser()
        page = await BrowserManager._context.new_page()
        page.set_default_timeout(60000)
        return BrowserManager._context, page

    async def launch_with_retry(self, max_retries: int = 2) -> Tuple:
        for attempt in range(max_retries + 1):
            try:
                return await self.launch()
            except Exception as e:
                if attempt < max_retries:
                    print(f"连接失败(第{attempt+1}次), 重试... {e}")
                    await asyncio.sleep(2)
                else:
                    raise

    async def close(self):
        """Worker 结束时关闭浏览器。"""
        await BrowserManager.shutdown()

    @classmethod
    async def shutdown(cls):
        """关闭浏览器——先优雅关闭，再强杀进程确保窗口消失。"""
        import subprocess as _sp
        try:
            if cls._browser:
                await cls._browser.close()
                await asyncio.sleep(0.5)
        except Exception:
            pass
        await cls._cleanup()
        # 确保 Edge 进程彻底退出
        try:
            _sp.run(["taskkill", "/F", "/IM", "msedge.exe"],
                    capture_output=True, timeout=5)
        except Exception:
            pass

    # ================================================================
    # 内部
    # ================================================================

    @classmethod
    async def _cleanup(cls):
        try:
            if cls._playwright:
                await cls._playwright.stop()
        except Exception:
            pass
        cls._browser = None
        cls._playwright = None
        cls._context = None

    async def _start_browser(self):
        """用 Playwright 启动系统 Edge（非 CDP），能正常关闭。"""
        from playwright.async_api import async_playwright

        BrowserManager._playwright = await async_playwright().start()

        proxy = self.settings.web_proxy if self.settings else None
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
        ]
        if proxy:
            args.append(f"--proxy-server={proxy}")
            args.append("--proxy-bypass-list=<-loopback>")

        BrowserManager._browser = await BrowserManager._playwright.chromium.launch(
            channel="msedge",
            headless=False,
            args=args,
        )

        ctx_opts = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        BrowserManager._context = await BrowserManager._browser.new_context(**ctx_opts)
        print("[BrowserManager] Edge 已启动")
