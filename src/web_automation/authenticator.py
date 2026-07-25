"""
HimmPat 登录管理模块（增强检测版）
"""
import asyncio
from pathlib import Path

from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


class Authenticator:
    """管理 HimmPat 登录状态和Session持久化"""

    def __init__(self, page, settings: Settings):
        self.page = page
        self.settings = settings
        self.human = HumanBehavior(settings)
        self._login_page_urls = ["login", "signin", "sign-in", "auth", "passport"]

    async def check_login(self) -> bool:
        """快速检查登录状态"""
        try:
            await self.page.goto(
                self.settings.himmpat_search_url,
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(0.5)

            # 方式1: URL 检测
            if any(s in self.page.url.lower() for s in self._login_page_urls):
                return False

            # 方式2: 任意 input 或搜索框存在
            try:
                inputs = await self.page.query_selector_all(
                    "textarea, input[type='text'], [contenteditable], "
                    "[class*='search'], [placeholder*='技术'], "
                    "[placeholder*='输入'], [placeholder*='检索']"
                )
                if inputs and len(inputs) > 0:
                    return True
            except Exception:
                pass

            # 方式3: 登录按钮消失 + 页面上有用户相关内容
            try:
                login_btn = await self.page.query_selector(
                    "a[href*='login'], button:has-text('登录')"
                )
                if not login_btn:
                    # 没有登录按钮了，大概率已登录
                    body = await self.page.evaluate(
                        "document.body?.innerText?.trim() || ''"
                    )
                    if len(body) > 100:  # 有实际内容
                        return True
            except Exception:
                pass

            # 方式4: 页面关键词检测（SPA 搜索页）
            try:
                body = await self.page.evaluate(
                    "document.body?.innerText?.trim() || ''"
                )
                if any(kw in body for kw in ["智能检索", "高级检索", "专利检索",
                                              "简单检索", "申请人", "发明人"]):
                    return True
            except Exception:
                pass

            # 方式5: localStorage 中有 user 对象
            try:
                has_user = await self.page.evaluate("""() => {
                    try {
                        const ls = window.localStorage;
                        return !!(ls.getItem('user') || ls.getItem('userInfo'));
                    } catch(e) { return false; }
                }""")
                if has_user:
                    return True
            except Exception:
                pass

            return False

        except Exception:
            return False

    async def manual_login(self, signals=None):
        """手动登录引导（多方式检测）"""
        base_url = self.settings.himmpat_base_url
        original_url = self.page.url

        if signals:
            signals.log.emit("INFO", "打开HimmPat登录页面...")

        # 导航到首页
        await self.page.goto(
            base_url, wait_until="domcontentloaded", timeout=15000
        )
        await asyncio.sleep(0.5)

        # 尝试点击登录按钮 — 优先小面积元素，排除大容器误匹配
        for s in ["a[href*='login']", "button:has-text('登录')",
                   "a:has-text('登录')", '[class*="login-btn"]',
                   '.login-btn', '[class*="loginBtn"]']:
            try:
                btn = await self.page.query_selector(s)
                if btn and await btn.is_visible():
                    box = await btn.bounding_box()
                    if box and box['width'] < 400:  # 排除误匹配的大容器
                        await self.human.human_click(self.page, selector=s)
                        await asyncio.sleep(0.5)
                        break
            except Exception:
                continue
        else:
            # 所有精确选择器都失败 → 用 JS 找小的"登录"文字元素
            try:
                clicked_js = await self.page.evaluate("""() => {
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
                        const text = el.textContent?.trim() || '';
                        const rect = el.getBoundingClientRect();
                        if (text === '登录' && rect.width < 200 && rect.width > 20) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if clicked_js:
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        if signals:
            signals.log.emit("WARN", "⏳ 请在浏览器中登录HimmPat，程序自动检测...")

        # 快速轮询检测
        waited = 0
        max_wait = 30 * 60  # 30分钟

        while waited < max_wait:
            url = self.page.url.lower()
            login_urls = ["login", "signin", "auth", "passport"]

            # === 检测方式1: URL 变了且不是登录页 ===
            if url != original_url.lower() and not any(
                s in url for s in login_urls
            ):
                # URL变化且不在登录页 → 大概率登录成功了
                # 再确认一下：页面上有可交互的输入框
                try:
                    inputs = await self.page.query_selector_all(
                        "textarea, input[type='text'], input[type='search']"
                    )
                    if inputs and len(inputs) > 0:
                        if signals:
                            signals.log.emit("SUCCESS", "✅ 检测到登录成功！")
                        await self._save_session()
                        return True
                except Exception:
                    pass

                # 没有输入框但有内容也行
                try:
                    body = await self.page.evaluate(
                        "document.body?.innerText?.trim() || ''"
                    )
                    if len(body) > 200:
                        if signals:
                            signals.log.emit("SUCCESS", "✅ 检测到页面已变更，登录成功！")
                        await self._save_session()
                        return True
                except Exception:
                    pass

            # === 检测方式2: 登录按钮消失 ===
            try:
                login_btn = await self.page.query_selector(
                    "a[href*='login'], button:has-text('登录')"
                )
                if not login_btn:
                    body = await self.page.evaluate(
                        "document.body?.innerText?.trim() || ''"
                    )
                    if len(body) > 200:
                        if signals:
                            signals.log.emit("SUCCESS", "✅ 登录按钮已消失，检测到登录成功！")
                        await self._save_session()
                        return True
            except Exception:
                pass

            # === 检测方式3: 页面文本中包含典型搜索页特征 ===
            try:
                body = await self.page.evaluate(
                    "document.body?.innerText?.trim() || ''"
                )
                if any(kw in body for kw in ["智能检索", "高级检索", "专利检索",
                                              "搜索结果", "patent", "PATENT",
                                              "申请人", "发明人", "公开"]):
                    if signals:
                        signals.log.emit("SUCCESS", "✅ 检测到专利检索页面，登录成功！")
                    await self._save_session()
                    return True
            except Exception:
                pass

            # 每 15 秒报一次状态
            if waited > 0 and waited % 15 == 0:
                if signals:
                    signals.log.emit("INFO",
                        f"⏳ 等待登录 ({waited}秒)... 请在浏览器中完成登录"
                    )

            await asyncio.sleep(0.5)
            waited += 0.5

        if signals:
            signals.log.emit("ERROR", "登录超时（30分钟）")
        return False

    async def auto_login(self) -> bool:
        """自动登录：填表 → 登录 → 跳转到搜索页"""
        username = self.settings.himmpat_username
        password = self.settings.himmpat_password
        search_url = self.settings.himmpat_search_url
        if not username or not password:
            return False

        # 直接跳到登录页
        login_url = self.settings.himmpat_login_url
        await self.page.goto(
            login_url,
            wait_until="domcontentloaded", timeout=15000
        )
        # 等页面完全加载
        try:
            await self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        # 填用户名
        ok_user = False
        for s in ["input[name='username']", "input[type='text']",
                   "input[name='phone']", "input[placeholder*='手机']",
                   "input[placeholder*='账号']",
                   # HimmPat SPA 输入框无 name/placeholder，用 type + 位置定位
                   "form input[type='text']:not([name=''])",
                   ".login-form input[type='text']",
                   "input[type='text']:first-of-type"]:
            try:
                el = await self.page.query_selector(s)
                if el and await el.is_visible():
                    await self.human.human_type(self.page, s, username)
                    ok_user = True
                    break
            except Exception:
                continue

        # 填密码
        ok_pwd = False
        for s in ["input[type='password']", "input[name='password']",
                   "form input[type='password']",
                   ".login-form input[type='password']"]:
            try:
                el = await self.page.query_selector(s)
                if el and await el.is_visible():
                    await self.human.human_type(self.page, s, password)
                    ok_pwd = True
                    break
            except Exception:
                continue

        # 如果常规选择器没找到，用 JS 直接捞（处理 SPA 无 name 属性的输入框）
        if not ok_user or not ok_pwd:
            try:
                inputs = await self.page.evaluate("""() => {
                    const all = document.querySelectorAll('input');
                    const textInput = Array.from(all).find(i =>
                        i.type === 'text' && i.offsetParent !== null
                    );
                    const pwdInput = Array.from(all).find(i =>
                        i.type === 'password' && i.offsetParent !== null
                    );
                    return textInput ? textInput.id || textInput.name || '' : '';
                }""")
                if inputs:
                    # 找到了第一个 text input，用 index 定位
                    await self.human.human_type(self.page, "input[type='text']", username)
                    await self.human.human_type(self.page, "input[type='password']", password)
                    ok_user = True
                    ok_pwd = True
            except Exception:
                pass

        if not ok_user or not ok_pwd:
            return False

        # 注册对话框自动处理（"已在其他地方登录"等确认弹窗）
        async def auto_accept_dialog(dialog):
            try:
                await dialog.accept()
            except Exception:
                pass
        self.page.on("dialog", auto_accept_dialog)

        # 点登录 — 等表单完全渲染
        await asyncio.sleep(1)
        clicked = False
        for s in ["div.box_btn", "div:has-text('登 录')",
                   '[class*="login-btn"]', "button[type='submit']",
                   "button:has-text('登录')", "a:has-text('登录')",
                   "form button", ".login-btn", '.box_btn']:
            try:
                btn = await self.page.query_selector(s)
                if btn and await btn.is_visible():
                    box = await btn.bounding_box()
                    if box and box['width'] < 600 and box['width'] > 50:  # 排除误匹配的大容器
                        await self.human.human_click(self.page, selector=s)
                        clicked = True
                        print(f"  Login button clicked: '{s}'")
                        break
            except Exception:
                continue

        # 如果按钮没找到，直接按 Enter 提交表单
        if not clicked:
            try:
                await self.page.keyboard.press("Enter")
            except Exception:
                pass

        # 处理"该账号已经在其他地方登录"弹窗
        await asyncio.sleep(2)  # 等弹窗完全渲染
        for retry in range(5):
            # 优先用 JS 直接找 el-message-box 中的"确定"按钮点击
            clicked = await self.page.evaluate("""() => {
                // Element UI message box 确定按钮
                const btns = document.querySelectorAll('.el-message-box__btns button');
                for (const btn of btns) {
                    if (btn.textContent.includes('确定') || btn.textContent.includes('继续')) {
                        btn.click();
                        return true;
                    }
                }
                // 兜底：找页面上可见的"确定"按钮
                const all = document.querySelectorAll('button');
                for (const btn of all) {
                    const t = btn.textContent || '';
                    if ((t.includes('确定') || t.includes('继续') || t.includes('确认'))
                        && btn.offsetParent !== null && btn.getBoundingClientRect().width < 600) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(2)
                # 如果 URL 已经跳走，说明登录完成
                if "login" not in self.page.url.lower() and "intelligence" in self.page.url:
                    break
            else:
                break  # 没有弹窗了

        # 等登录完成 — 等待页面自动跳离 /login 或收到登录成功的 API 响应
        await asyncio.sleep(2)
        try:
            # 先等页面 URL 变化（从 /login 跳走）
            await self.page.wait_for_function(
                "() => !window.location.pathname.includes('login')",
                timeout=15000
            )
        except Exception:
            # 超时 -> 可能是 SPA 登录，检查有没有 API 调用成功
            pass

        # 如果还没跳转，主动跳转到搜索页
        try:
            await self.page.goto(
                search_url, wait_until="domcontentloaded", timeout=15000
            )
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1)

        # 确认登录成功：多种信号综合判断
        if "login" not in self.page.url.lower():
            # 信号1: 有标准的 input/textarea
            boxes = await self.page.query_selector_all(
                "textarea, input[type='text']"
            )
            if boxes and len(boxes) > 0:
                await self._save_session()
                return True

            # 信号2: 页面上有用户信息（HimmPat SPA 的搜索页可能用 div 模拟输入框）
            body_text = await self.page.evaluate(
                "document.body?.innerText?.trim() || ''"
            )
            if any(kw in body_text for kw in
                   ["智能检索", "高级检索", "专利检索", "简单检索",
                    "搜索结果", "申请人", "发明人", "公开（公告）"]):
                await self._save_session()
                return True

            # 信号3: localStorage 中有 user 对象（已登录标志）
            has_user = await self.page.evaluate("""() => {
                try {
                    const ls = window.localStorage;
                    return !!(ls.getItem('user') || ls.getItem('userInfo'));
                } catch(e) { return false; }
            }""")
            if has_user:
                await self._save_session()
                return True

        return False

    async def _save_session(self):
        try:
            profile_dir = Path(self.settings.session_profile_dir)
            if not profile_dir.is_absolute():
                profile_dir = Path.cwd() / profile_dir
            profile_dir.mkdir(parents=True, exist_ok=True)
            await self.page.context.storage_state(
                path=str(profile_dir / "storage_state.json")
            )
        except Exception as e:
            print(f"保存Session失败: {e}")
