"""
检索执行器 — 简化但完整版
"""
import asyncio
from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


class Searcher:
    def __init__(self, page, settings: Settings, human: HumanBehavior):
        self.page = page
        self.settings = settings
        self.human = human
        self._input_sel = None

    async def _find_input(self) -> str:
        """找搜索框（HimmPat SPA 用 div[contenteditable] 模拟输入框）"""
        if self._input_sel:
            return self._input_sel
        # 优先使用配置中的选择器（可定位中文 placeholder 的搜索框）
        cfg_sel = self.settings.himmpat_selectors.get("search_input", "")
        configured = [cfg_sel] if cfg_sel else []
        # 按权重找
        for s in [
            *configured,
            "div[contenteditable].editable-div",
            "div.editable-div",
            "[contenteditable]",
            "textarea",
            "input[type='text']",
            "[role='textbox']",
            "div.search-box-change",
        ]:
            try:
                el = await self.page.query_selector(s)
                if el and await el.is_visible():
                    box = await el.bounding_box()
                    if box and box["y"] < 100:
                        continue  # 跳过顶部导航栏
                    self._input_sel = s
                    return s
            except Exception:
                continue
        self._input_sel = "[contenteditable]"
        return "[contenteditable]"

    async def execute_search(self, query: str, idx: int) -> tuple:
        """执行一条检索式"""
        search_url = self.settings.himmpat_search_url

        # 导航到搜索页（首次或URL不对时）
        if idx == 1 or search_url not in self.page.url:
            await self.page.goto(search_url, timeout=20000)
            await asyncio.sleep(2)

        # 找搜索框并输入
        sel = await self._find_input()
        await self.human.human_type(self.page, sel, query)
        await asyncio.sleep(0.5)

        # 优先点击搜索按钮（HimmPat 的 div[contenteditable] 可能不响应 Enter）
        btn_sel = self.settings.himmpat_selectors.get("search_button", "button.search-btn")
        clicked = False
        for s in [btn_sel, "button[type='submit']", '[class*="search"] button',
                   "button:has-text('搜索')", "button:has-text('检索')"]:
            try:
                btn = await self.page.query_selector(s)
                if btn and await btn.is_visible():
                    await self.human.human_click(self.page, selector=s)
                    clicked = True
                    break
            except Exception:
                continue

        # 找不到搜索按钮时 fallback 到 Enter 键
        if not clicked:
            await self.page.keyboard.press("Enter")
        await asyncio.sleep(1)

        # 处理"检索式可能有误"弹窗
        try:
            warn_btn = await self.page.query_selector(
                ".el-message-box__btns button:has-text('确定'), "
                ".el-message-box__btns button:has-text('继续'), "
                "button:has-text('确认继续')"
            )
            if warn_btn and await warn_btn.is_visible():
                await warn_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # 等结果出现
        for i in range(20):
            txt = await self.page.evaluate("document.body?.innerText || ''")
            if len(txt) > 100 and ("CN" in txt or "找到" in txt or "共" in txt):
                await asyncio.sleep(1)
                break
            await asyncio.sleep(1)

        await asyncio.sleep(1)

        # 提取结果
        results = await self._extract()
        return (len(results), results)

    async def _extract(self) -> list[dict]:
        """提取检索结果"""
        import re
        txt = await self.page.evaluate("document.body?.innerText?.trim() || ''")

        results = []
        seen = set()
        # 找 CN 专利号
        for m in re.finditer(r'(CN\s*\d{4,}[A-Z]?)', txt):
            pn = m.group(1).replace(" ", "")
            if pn not in seen:
                seen.add(pn)
                results.append({"publication_number": pn})
            if len(results) >= 50:
                break
        return results
