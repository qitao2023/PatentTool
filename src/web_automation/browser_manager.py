"""
浏览器管理器 - 单例模式，全程共用一个浏览器。

使用 Playwright 直接启动系统安装的浏览器（chrome/msedge/firefox），
而非 CDP 连接，确保 browser.close() 能真正关闭浏览器窗口。

三个浏览器轮换：全部 403 后自动冷却 1 小时再继续。
"""
import asyncio
import time
from pathlib import Path
from typing import Tuple

from src.utils.config import Settings


class BrowserManager:
    """浏览器单例管理器。Playwright 直接管理浏览器生命周期。"""

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
        """获取浏览器 context 和 page。如处于冷却期则等待冷却结束。"""
        # 检查全局冷却
        await BrowserManager._check_cooldown()

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
        # CDP 自启动的浏览器进程
        if cls._cdp_process:
            try:
                cls._cdp_process.terminate()
                cls._cdp_process.wait(timeout=5)
            except Exception:
                try:
                    cls._cdp_process.kill()
                except Exception:
                    pass
            cls._cdp_process = None
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
    _force_channel = None
    _cdp_process = None

    @classmethod
    async def _launch_chrome_for_cdp(cls, port: int):
        """用系统命令启动 Chrome，带上 --remote-debugging-port。

        这样启动的 Chrome 和用户手动打开完全一样——没有 Playwright 的
        automation 标记、视口限制，PATENTSCOPE 页面渲染正常。
        """
        import subprocess
        import shutil

        # 找 Chrome 可执行文件路径
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("chrome"),
            shutil.which("google-chrome"),
        ]

        # 如果配置的是 msedge
        configured = cls._force_channel or "chrome"
        if configured == "msedge":
            chrome_paths = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                shutil.which("msedge"),
            ] + chrome_paths

        exe = None
        for p in chrome_paths:
            if p and Path(p).exists():
                exe = p
                break

        if not exe:
            raise RuntimeError("未找到 Chrome/Edge 可执行文件，无法启动 CDP 模式")

        # 使用独立的用户数据目录，避免和用户正常浏览器冲突
        profile_dir = Path.cwd() / "profiles" / "cdp_browser"
        profile_dir.mkdir(parents=True, exist_ok=True)

        print(f"[BrowserManager] 启动: {exe} --remote-debugging-port={port}")
        cls._cdp_process = subprocess.Popen(
            [exe, f"--remote-debugging-port={port}",
             f"--user-data-dir={profile_dir}",
             "--no-first-run", "--no-default-browser-check",
             "--disable-session-crashed-bubble",
             "about:blank"],  # 打开空白页快速启动
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # 等 Chrome 启动并监听调试端口
        import time as _time
        for _ in range(15):  # 最多等 15 秒
            await asyncio.sleep(1)
            try:
                import urllib.request as _req
                resp = _req.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2)
                resp.close()
                print("[BrowserManager] Chrome 启动完成，调试端口已就绪")
                return
            except Exception:
                pass

        raise RuntimeError(f"Chrome 启动超时 (15s)，端口 {port} 无响应")

    # ── 全局 403 冷却机制 ──
    _channels_failed_403: set = set()  # 本轮已报403的浏览器
    _cooldown_until: float = 0.0       # 冷却结束时间戳（Unix time）
    COOLDOWN_SECONDS = 3600            # 冷却时长：1 小时

    @classmethod
    def switch_channel(cls, on_403: bool = False):
        """切换到另一个浏览器。

        Args:
            on_403: True=因403切换（追踪用于全局冷却），False=主动轮换（不限流）
        """
        # ── 403 追踪 ──
        if on_403:
            # 当前正在用的浏览器（切换前）
            current = (cls._force_channel
                       or cls._CHANNELS[cls._channel_index])
            cls._channels_failed_403.add(current)

            failed_n = len(cls._channels_failed_403)
            total_n = len(cls._CHANNELS)
            print(f"[BrowserManager] {current} 403 "
                  f"({failed_n}/{total_n} 通道已失效)")

            # 三个全403 → 进入冷却
            if failed_n >= total_n:
                cls._cooldown_until = time.time() + cls.COOLDOWN_SECONDS
                mins = cls.COOLDOWN_SECONDS // 60
                print(f"[BrowserManager] ⚠️ 三个浏览器全部403！"
                      f"进入冷却 {mins} 分钟，暂停所有操作...")
                return None  # 无可用通道

        # ── 切换 ──
        cls._channel_index = (cls._channel_index + 1) % len(cls._CHANNELS)
        name = cls._CHANNELS[cls._channel_index]
        cls._force_channel = name
        if not on_403:
            print(f"[BrowserManager] 主动轮换: {name}")
        else:
            print(f"[BrowserManager] 403 切换: {name}")
        return name

    @classmethod
    def _is_in_cooldown(cls) -> bool:
        """是否正在冷却期"""
        return time.time() < cls._cooldown_until

    @classmethod
    def get_cooldown_remaining(cls) -> float:
        """冷却剩余秒数，0=不在冷却期"""
        if not cls._is_in_cooldown():
            return 0.0
        return max(0.0, cls._cooldown_until - time.time())

    @classmethod
    async def _check_cooldown(cls):
        """如果处于冷却期，阻塞等待冷却结束再继续"""
        if not cls._is_in_cooldown():
            return
        remain = cls._cooldown_until - time.time()
        if remain <= 0:
            cls._cooldown_until = 0.0
            cls._channels_failed_403.clear()
            cls._channel_index = 0
            print("[BrowserManager] ✅ 冷却结束，重置浏览器通道")
            return

        mins = remain / 60
        print(f"[BrowserManager] 🕐 处于冷却期（剩余 {mins:.1f} 分钟），"
              f"等待中...")
        await asyncio.sleep(remain + 2)  # 多等2秒确保冷却完全结束
        cls._cooldown_until = 0.0
        cls._channels_failed_403.clear()
        cls._channel_index = 0
        print("[BrowserManager] ✅ 冷却结束，重置浏览器通道，继续工作")

    async def _start_browser(self):
        """启动浏览器，支持 Chrome/Edge/Firefox 轮换，或 CDP 连接已打开的浏览器。"""
        from playwright.async_api import async_playwright

        # ── CDP 模式：尝试连接已打开的浏览器，失败则自动启动 ──
        use_cdp = self.settings.web_use_cdp if self.settings else False
        if use_cdp:
            cdp_port = self.settings.web_cdp_port if self.settings else 9222
            BrowserManager._playwright = await async_playwright().start()

            # 先尝试连接已运行的浏览器
            try:
                BrowserManager._browser = await BrowserManager._playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{cdp_port}"
                )
                print(f"[BrowserManager] CDP 已连接: 127.0.0.1:{cdp_port}")
            except Exception:
                # 没连上 → 自动启动 Chrome（系统进程，非 Playwright 模式）
                print(f"[BrowserManager] CDP 未连接，自动启动 Chrome...")
                await BrowserManager._playwright.stop()
                BrowserManager._playwright = None
                try:
                    await BrowserManager._launch_chrome_for_cdp(cdp_port)
                    # 重新连接
                    BrowserManager._playwright = await async_playwright().start()
                    BrowserManager._browser = await BrowserManager._playwright.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{cdp_port}"
                    )
                    print(f"[BrowserManager] CDP 已连接 (自启动): 127.0.0.1:{cdp_port}")
                except Exception as e2:
                    # Chrome 启动也失败 → 降级为普通 Playwright 模式
                    print(f"[BrowserManager] Chrome 自启动失败 ({e2})，降级为普通启动")
                    use_cdp = False

            # CDP 模式：使用浏览器已有的默认 context
            ctx = BrowserManager._browser.contexts
            if ctx:
                BrowserManager._context = ctx[0]
            else:
                BrowserManager._context = await BrowserManager._browser.new_context()
            page = await BrowserManager._context.new_page()
            page.set_default_timeout(60000)
            print("[BrowserManager] CDP 模式就绪（渲染与手动浏览一致）")
            return

        # ── 正常启动模式 ──
        BrowserManager._playwright = await async_playwright().start()
        ...

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
        print(f"[BrowserManager] {channel} 已启动")
