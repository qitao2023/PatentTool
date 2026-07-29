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
        """关闭浏览器。"""
        await BrowserManager.shutdown()

    @classmethod
    async def rotate_proxy(cls, settings=None):
        """尝试切换 Clash 代理节点换 IP。返回 True 表示已切换。"""
        if not settings:
            return False
        clash_api = getattr(settings, 'web_clash_api', None)
        if not clash_api:
            return False
        try:
            import urllib.request as _req
            # 获取所有代理节点
            resp = _req.urlopen(f"{clash_api}/proxies", timeout=5)
            import json as _json
            data = _json.loads(resp.read())
            proxies = data.get("proxies", {})
            # 找可切换的节点组
            for group_name, group_info in proxies.items():
                if group_info.get("type") == "Selector" and group_info.get("now"):
                    nodes = group_info.get("all", [])
                    if len(nodes) > 1:
                        # 随机选一个不同于当前的节点
                        import random as _rnd
                        current = group_info["now"]
                        others = [n for n in nodes if n != current]
                        if others:
                            new_node = _rnd.choice(others)
                            put_req = _req.Request(
                                f"{clash_api}/proxies/{group_name}",
                                data=_json.dumps({"name": new_node}).encode(),
                                method="PUT",
                                headers={"Content-Type": "application/json"})
                            _req.urlopen(put_req, timeout=5)
                            print(f"[BrowserManager] Clash 切换节点: {current} → {new_node}")
                            return True
        except Exception:
            pass
        return False

    @classmethod
    async def shutdown(cls):
        """关闭浏览器。"""
        try:
            if cls._browser:
                await cls._browser.close()
        except Exception:
            pass
        await cls._cleanup()

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

    _channel_index = 0
    _CHANNELS = ["chrome", "msedge", "firefox"]
    _force_channel = None     # 轮换时强制指定的浏览器，优先级最高

    @classmethod
    def switch_channel(cls):
        """切换到另一个浏览器，返回新浏览器名"""
        cls._channel_index = (cls._channel_index + 1) % len(cls._CHANNELS)
        name = cls._CHANNELS[cls._channel_index]
        cls._force_channel = name  # 强制下一次启动用这个
        print(f"[BrowserManager] 切换到: {name}")
        return name

    async def _start_browser(self):
        """启动浏览器，支持 Chrome/Edge/Firefox 轮换。"""
        from playwright.async_api import async_playwright

        BrowserManager._playwright = await async_playwright().start()

        proxy = self.settings.web_proxy if self.settings else None

        # 优先级：轮换强制 > 用户配置
        if BrowserManager._force_channel:
            channel = BrowserManager._force_channel
            BrowserManager._force_channel = None  # 只用一次
        else:
            configured = self.settings.web_browser if self.settings else "chrome"
            channel = configured if configured in BrowserManager._CHANNELS else "chrome"

        if channel == "firefox":
            launch_opts = {"headless": False}
            if proxy:
                launch_opts["proxy"] = {"server": proxy}
            BrowserManager._browser = await BrowserManager._playwright.firefox.launch(**launch_opts)
        else:
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
                channel=channel,
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
