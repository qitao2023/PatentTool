"""
浏览器管理器 - 使用临时 Profile + Cookie 恢复，彻底避免锁冲突
"""
import asyncio
import os
import signal
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Tuple

from src.utils.config import Settings


STORAGE_FILE = "storage_state.json"


class BrowserManager:
    """管理 Stealth 浏览器实例"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._context = None
        self._page = None
        self._playwright = None
        self._profile_dir = None
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

    def _kill_chrome(self):
        """杀掉所有 chrome.exe"""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe"],
                capture_output=True, timeout=10
            )
        except Exception:
            pass

    def _storage_path(self) -> Path:
        """持久化 Cookie 文件路径"""
        p = Path(self.settings.session_profile_dir)
        if not p.is_absolute():
            p = Path.cwd() / p
        p.mkdir(parents=True, exist_ok=True)
        return p / STORAGE_FILE

    async def launch(self) -> Tuple:
        """启动浏览器（Edge，每次用全新临时目录）"""
        from playwright.async_api import async_playwright

        self._kill_chrome()

        self._playwright = await async_playwright().start()

        self._profile_dir = Path(tempfile.mkdtemp(prefix="pp_"))

        # 使用 Edge (msedge channel) — 系统自带浏览器，指纹更真实
        # 注意: launch_persistent_context 不支持 args 中传 URL，只能 new_page + goto
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            channel="msedge",
            headless=False,
            locale=self.settings.web_locale,
            timezone_id=self.settings.web_timezone,
            no_viewport=True,  # 最大化后自动适配实际窗口尺寸
            args=[
                "--start-maximized",
                "--disable-password-manager-reauthentication",
                "--disable-save-password-bubble",
            ],
        )

        # 恢复已保存的 Cookie
        storage_path = self._storage_path()
        if storage_path.exists():
            try:
                with open(storage_path, "r") as f:
                    import json
                    data = json.load(f)
                if "cookies" in data:
                    await self._context.add_cookies(data["cookies"])
            except Exception:
                pass

        # Stealth 脚本
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get:()=>undefined});
            Object.defineProperty(navigator, 'plugins', {get:()=>{
                const p=[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}];
                p.item=i=>p[i]; p.length=p.length; return p;
            }});
            Object.defineProperty(navigator, 'languages', {get:()=>['zh-CN','zh','en-US','en']});
            Object.defineProperty(navigator, 'platform', {get:()=>'Win32'});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get:()=>8});
        """)

        self._page = await self._context.new_page()
        self._page.set_default_timeout(30000)

        # 自动接受所有浏览器对话框（alert/confirm/prompt）
        self._page.on("dialog", lambda d: d.accept() if d.type != "prompt" else d.accept(""))

        # 立即跳转到目标站
        try:
            await self._page.goto(
                self.settings.himmpat_base_url,
                wait_until="commit", timeout=15000  # commit 比 domcontentloaded 更快
            )
        except Exception:
            pass

        return self._context, self._page

    async def save_storage(self):
        """保存 Cookie 到文件"""
        try:
            state = await self._context.storage_state()
            storage_path = self._storage_path()
            with open(storage_path, "w") as f:
                import json
                json.dump(state, f)
        except Exception:
            pass

    async def close(self):
        """关闭浏览器"""
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        # 清理临时目录
        if self._profile_dir and self._profile_dir.exists():
            try:
                shutil.rmtree(str(self._profile_dir), ignore_errors=True)
            except Exception:
                pass
        # 清理残留 chrome
        self._kill_chrome()

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
