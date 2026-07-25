"""
专利详情获取模块 — 在搜索结果页上点击专利链接，打开详情页提取全文
"""
import asyncio
from typing import Optional

from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


class PatentFetcher:
    """专利详情获取器：在搜索结果页上点击每个专利，提取全文"""

    def __init__(self, page, settings: Settings, human: HumanBehavior):
        self.page = page
        self.settings = settings
        self.human = human
        self._popup_page = None  # 跟踪从点击打开的弹窗/新标签页

    async def fetch_from_results_page(self, patent_number: str,
                                       signals=None, query_index: int = 0,
                                       total: int = 0) -> Optional[dict]:
        """
        在当前搜索结果页，找到指定专利的链接并点击进入详情页

        优先从当前结果页直接点击专利链接；失败时 fallback 到搜索专利号。
        """
        if signals:
            signals.log.emit("INFO",
                f"  打开 {patent_number} ({query_index}/{total})")

        # 尝试1: 从当前页面点击专利链接（不离开结果页）
        clicked = await self._click_with_popup_handling(patent_number)

        if clicked:
            content = await self._extract_detail_page()
            content["publication_number"] = patent_number
            # 回到结果页
            await self._go_back_to_results()
            return content

        # 尝试2: fallback — 搜索专利号（原有逻辑）
        if signals:
            signals.log.emit("INFO",
                f"  未在当前页找到专利链接，搜索 {patent_number} ...")
        await self._search_patent(patent_number)
        await asyncio.sleep(2)

        content = await self._extract_detail_page()
        content["publication_number"] = patent_number
        return content

    async def _search_patent(self, patent_number: str):
        """在HimmPat上搜索特定专利号"""
        search_url = self.settings.himmpat_search_url
        await self.page.goto(search_url, wait_until="domcontentloaded",
                             timeout=15000)
        await asyncio.sleep(1)

        # 找搜索框
        sel = self.settings.himmpat_selectors.get("search_input",
                                                   "textarea.search-input")
        try:
            await self.page.wait_for_selector(sel, timeout=10000, state="visible")
        except Exception:
            alt = ["input[type='text']", "textarea", ".search-box input"]
            for s in alt:
                el = await self.page.query_selector(s)
                if el:
                    sel = s
                    break

        # 输入专利号并搜索
        await self.human.human_type(self.page, sel, patent_number)
        await asyncio.sleep(0.5)

        # 点击搜索按钮
        btn_sel = self.settings.himmpat_selectors.get("search_button",
                                                       "button.search-btn")
        for s in [btn_sel, "button[type='submit']", '[class*="search"]',
                   "button:has-text('搜索')"]:
            try:
                btn = await self.page.query_selector(s)
                if btn and await btn.is_visible():
                    await self.human.human_click(self.page, selector=s)
                    break
            except Exception:
                continue
        else:
            await self.page.keyboard.press("Enter")

        await asyncio.sleep(2)

        # 搜索结果出来后，点第一个结果的标题链接进入详情
        link_selectors = [
            "a[href*='detail']",
            "a[href*='patent']",
            ".patent-title a",
            ".title a",
            '[class*="title"] a',
            "a:has-text('" + patent_number[:8] + "')",
        ]
        for s in link_selectors:
            try:
                links = await self.page.query_selector_all(s)
                if links and len(links) > 0:
                    await self.human.human_click(self.page, selector=s)
                    await asyncio.sleep(2)
                    return
            except Exception:
                continue

        # 都找不到链接，可能已经在详情页了
        if patent_number[:8] in self.page.url:
            return

    async def _click_patent_on_current_page(self, patent_number: str) -> bool:
        """
        在当前搜索结果页上找含有该专利号的链接并点击（不进新标签）
        """
        # 策略1: CSS 选择器定位
        for s in [
            f"a:has-text('{patent_number}')",
            f"a[href*='{patent_number[:8]}']",
            f"a[href*='{patent_number[:6]}']",
            f"[class*='patent'] a:has-text('{patent_number[:6]}')",
            f"td:has-text('{patent_number}') a",
            f"a[class*='title']",
            f".patent-title a",
        ]:
            try:
                el = await self.page.query_selector(s)
                if el and await el.is_visible():
                    await self.human.human_click(self.page, selector=s)
                    await asyncio.sleep(2)
                    return True
            except Exception:
                continue

        # 策略2: JS 找含专利号的行，然后点该行内的标题链接
        try:
            clicked = await self.page.evaluate(f"""() => {{
                const num = '{patent_number}';
                // 找所有包含专利号的文本节点
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                let node;
                while (node = walker.nextNode()) {{
                    if (node.textContent.includes(num)) {{
                        // 向上找行容器（约 8 层足够）
                        let row = node.parentElement;
                        let depth = 0;
                        while (row && row !== document.body && depth < 8) {{
                            // 在行容器内找可点击的标题链接
                            const links = row.querySelectorAll('a');
                            for (const link of links) {{
                                const text = link.textContent.trim();
                                const href = link.href || '';
                                // 链接内容不能只有数字/空白，且不能是专利号本身
                                if (text.length > 5 && !text.startsWith('CN')
                                    && (href.includes('detail') || href.includes('patent')
                                        || link.classList.contains('title')
                                        || /[一-鿿]/.test(text))) {{
                                    link.click();
                                    return true;
                                }}
                            }}
                            row = row.parentElement;
                            depth++;
                        }}
                    }}
                }}
                return false;
            }}""")
            if clicked:
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        # 策略3: 旧 JS — 含专利号的祖先点击元素（兜底）
        try:
            clicked = await self.page.evaluate(f"""() => {{
                const num = '{patent_number}';
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                let node;
                while (node = walker.nextNode()) {{
                    if (node.textContent.includes(num)) {{
                        let el = node.parentElement;
                        while (el && el !== document.body) {{
                            if (el.tagName === 'A' || el.tagName === 'BUTTON'
                                || el.onclick || el.getAttribute('role') === 'button'
                                || el.classList.contains('patent-title')
                                || el.classList.contains('title')) {{
                                el.click();
                                return true;
                            }}
                            el = el.parentElement;
                        }}
                    }}
                }}
                return false;
            }}""")
            if clicked:
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        return False

    async def _go_back_to_results(self):
        """从详情页回到搜索结果页"""
        try:
            # 如果有弹窗页，先关掉
            if self._popup_page:
                try:
                    await self._popup_page.close()
                except Exception:
                    pass
                self._popup_page = None
                return  # 弹窗页不涉及页面跳转

            # 当前页面后退
            await self.page.go_back(wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(2)  # 等 SPA 重新渲染
        except Exception:
            # 后退失败，直接导航回搜索页
            try:
                await self.page.goto(
                    self.settings.himmpat_search_url,
                    wait_until="domcontentloaded", timeout=15000
                )
                await asyncio.sleep(2)
            except Exception:
                pass

    async def _click_with_popup_handling(self, patent_number: str) -> bool:
        """点击专利链接，处理可能的新标签/弹窗打开"""
        new_page = None

        async def on_page(page):
            nonlocal new_page
            new_page = page
            try:
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass

        try:
            self.page.context.on("page", on_page)

            # 尝试点击
            clicked = await self._click_patent_on_current_page(patent_number)

            if new_page:
                # 链接在新标签打开
                self._popup_page = new_page
                return True
            return clicked
        finally:
            # 移除监听器（Python 方式：重新绑定一个空的）
            try:
                self.page.context.remove_listener("page", on_page)
            except Exception:
                pass

    async def _extract_detail_page(self) -> dict:
        """从专利详情页提取内容（优先用弹窗/新标签页，否则用当前页）"""
        await asyncio.sleep(1)

        target = self._popup_page if self._popup_page else self.page

        full_text = await target.evaluate(
            "document.body?.innerText?.trim() || ''"
        )

        structured = await target.evaluate("""
            () => {
                const g = (s) => document.querySelector(s)?.textContent?.trim() || '';
                return {
                    title: g('h1') || g('h2') || g('[class*="title"]'),
                    claims: (g('[class*="claim"]') || g('#claims') || '').slice(0,20000),
                    description: (g('[class*="description"]') || g('[class*="detail"]') || '').slice(0,30000),
                };
            }
        """) or {}

        result = {
            "full_text": full_text[:50000],
            "title": structured.get("title", ""),
            "claims": structured.get("claims", ""),
            "description": structured.get("description", ""),
        }

        # 如果结构化没提取到，从全文里用正则找
        if not result["claims"] and full_text:
            import re
            m = re.search(r'权[利力]要求[书]?\s*', full_text)
            if m:
                end = re.search(r'说\s*明\s*书|技术领域|附图说明',
                                full_text[m.end():])
                result["claims"] = full_text[m.end():m.end()+end.start()][:20000] if end else full_text[m.end():][:20000]
            m = re.search(r'说\s*明\s*书|技术领域', full_text)
            if m:
                result["description"] = full_text[m.start():][:30000]

        return result

    async def fetch_batch(self, patents: list[dict],
                           signals=None, max_count: int = 15) -> list[dict]:
        """批量获取专利详情"""
        enriched = []
        total = min(len(patents), max_count)

        for idx, pat in enumerate(patents[:max_count], 1):
            pn = pat.get("publication_number", "")
            if not pn:
                enriched.append(pat)
                continue

            detail = await self.fetch_from_results_page(
                pn, signals, query_index=idx, total=total
            )
            if detail:
                pat["full_text"] = detail.get("full_text", "")
                pat["claims_full"] = detail.get("claims", "")
                pat["description_full"] = detail.get("description", "")
            enriched.append(pat)

            if idx < total:
                await self.human.inter_search_delay(idx)

        return enriched
