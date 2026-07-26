"""
HimmPat 集成爬虫 — 统一处理「检索 → 分页 → 逐条点击 → 提取详情 → 返回 → 下一条」

完整流程:
  对每条检索式:
    1. 输入检索式 → 点击检索
    2. 等待结果页加载
    3. 对当前页的每个专利:
       a. 点击专利标题链接 → 进入详情页（新标签/SPA导航/弹窗）
       b. 提取全文（标题、权利要求、说明书等）
       c. 返回结果列表页
       d. 计数器+1，达到50或该页末 → 翻页
    4. 翻页 → 重复步骤3
    5. 全部完成或满50条 → 下一条检索式
"""
import asyncio
import re
import time
from typing import Optional

from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


class HimmPatScraper:
    """HimmPat 检索+详情一体化爬虫"""

    def __init__(self, page, settings: Settings, human: HumanBehavior):
        self.page = page
        self.settings = settings
        self.human = human
        self._popup_page = None  # 追踪新标签页
        self._search_input_sel = None
        self._search_btn_sel = None
        self._current_page = 1

    # ================================================================
    # 公开 API
    # ================================================================

    async def execute_query(self, query: str, query_index: int = 1,
                            max_results: int = 50,
                            signals=None) -> list[dict]:
        """
        执行一条检索式，逐页逐条点击提取详情。

        流程:
          1. 导航到搜索页 + 输入检索式 + 点击检索
          2. 对当前页: 获取所有专利条目（含专利号+标题）
          3. 按专利号逐条点击 → 提取详情 → 返回结果列表
          4. 翻页 → 重复步骤2-3
          5. 满 max_results 或全部完成 → 返回

        Returns:
            list[dict]: 每项包含 publication_number, title, full_text,
                        claims, description, abstract, ipc, applicant,
                        publication_date 等
        """
        enriched = []
        self._current_page = 1
        processed_on_page = set()
        self._results_url = None  # 保存结果列表页 URL，返回时直接导航到此

        # Step 1: 导航到搜索页 + 执行检索
        await self._navigate_and_search(query, query_index)
        # 保存结果页 URL（通常是 /list），后续返回时用
        self._results_url = self.page.url

        # Step 2: 逐页处理
        while len(enriched) < max_results:
            if signals:
                signals.log.emit("INFO",
                    f"  📄 检索式{query_index} 第{self._current_page}页: "
                    f"已收集 {len(enriched)}/{max_results} 篇")

            # 等页面渲染
            await self._wait_for_results()

            # 获取当前页的专利条目（先收集所有专利号，再逐条点击）
            page_items = await self._find_result_items()
            if not page_items:
                if signals:
                    signals.log.emit("WARN",
                        f"  第{self._current_page}页未找到专利结果条目")
                break

            # 过滤掉本页已处理过的
            pending_items = [
                item for item in page_items
                if item.get("publication_number") not in processed_on_page
            ]

            if signals:
                signals.log.emit("INFO",
                    f"  第{self._current_page}页: {len(page_items)} 个条目, "
                    f"待处理 {len(pending_items)} 个")

            # 逐条点击 → 提取详情 → 返回
            processed_on_page = set()
            for item in pending_items:
                if len(enriched) >= max_results:
                    break

                pn = item.get("publication_number", "")
                title_hint = (item.get("title") or "")[:40]

                if signals:
                    signals.log.emit("INFO",
                        f"    🖱 点击 [{len(enriched)+1}/{max_results}] {pn} ...")

                result = await self._click_and_extract_one(
                    patent_number=pn,
                    title_hint=item.get("title", ""),
                    query_index=query_index,
                    global_index=len(enriched) + 1,
                )

                if result:
                    if not result.get("publication_number"):
                        result["publication_number"] = pn
                    enriched.append(result)
                    processed_on_page.add(pn)
                    if signals:
                        rtitle = (result.get("title") or title_hint)[:50]
                        signals.log.emit("INFO",
                            f"    ✅ [{len(enriched)}/{max_results}] "
                            f"{pn}: {rtitle}")
                else:
                    # 点击详情失败，用搜索结果页已有的摘要数据兜底
                    fallback = {
                        "publication_number": pn,
                        "title": item.get("title", ""),
                        "abstract": item.get("abstract", ""),
                        "full_text": item.get("abstract", ""),
                        "claims": "",
                        "description": "",
                    }
                    enriched.append(fallback)
                    processed_on_page.add(pn)
                    if signals:
                        rtitle = (item.get("title") or "")[:50]
                        signals.log.emit("WARN",
                            f"    ⚠ {pn}: 详情页提取失败，使用摘要数据: {rtitle}")

                # 返回结果列表
                await self._go_back_to_results()
                await asyncio.sleep(1)

                # 验证返回成功
                if len(enriched) < max_results and \
                   pending_items and item != pending_items[-1]:
                    check_items = await self._find_result_items()
                    if not check_items:
                        if signals:
                            signals.log.emit("WARN",
                                "  返回结果页异常，用保存的URL恢复...")
                        # 用保存的结果页 URL 而不是重新搜索
                        if self._results_url:
                            try:
                                await self.page.goto(
                                    self._results_url,
                                    wait_until="domcontentloaded", timeout=15000)
                                await asyncio.sleep(2)
                            except Exception:
                                pass
                        # 如果还是没有，才重新搜索
                        check_items = await self._find_result_items()
                        if not check_items:
                            await self._navigate_and_search(query, query_index)
                            await self._wait_for_results()
                            self._results_url = self.page.url
                        page_items = await self._find_result_items()
                        pending_items = [
                            it for it in page_items
                            if it.get("publication_number") not in processed_on_page
                        ]

            # 翻页
            if len(enriched) >= max_results:
                break

            has_next = await self._go_to_next_page()
            if not has_next:
                if signals:
                    signals.log.emit("INFO",
                        f"  检索式{query_index}: 已到最后一页")
                break
            self._current_page += 1
            self._results_url = self.page.url  # 更新为当前页 URL
            processed_on_page = set()
            await asyncio.sleep(2)

        return enriched

    # ================================================================
    # Step 1: 导航 + 检索
    # ================================================================

    async def _navigate_and_search(self, query: str, query_index: int):
        """导航到搜索页并执行检索"""
        search_url = self.settings.himmpat_search_url

        # 首次或 URL 不对时导航
        current_url = self.page.url
        if query_index == 1 or search_url not in current_url:
            try:
                await self.page.goto(search_url, timeout=20000)
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(2)

        # Step A: 找到搜索框并填入检索式
        sel = await self._find_search_input()
        await self._fill_search_input(sel, query)
        await asyncio.sleep(0.5)

        # Step B: 等搜索按钮变为可用状态，然后点击
        await self._click_search_button()
        await asyncio.sleep(1)

        # 处理"检索式可能有误"弹窗
        try:
            clicked = await self.page.evaluate("""() => {
                const btns = document.querySelectorAll(
                    '.el-message-box__btns button, .el-dialog__footer button');
                for (const btn of btns) {
                    const t = btn.textContent || '';
                    if (t.includes('确定') || t.includes('继续') || t.includes('确认')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(0.5)
        except Exception:
            pass

    async def _find_search_input(self) -> str:
        """找搜索框选择器"""
        if self._search_input_sel:
            return self._search_input_sel

        candidates = [
            # contenteditable div (HimmPat SPA 常见)
            "div[contenteditable='true']",
            "div.editable-div",
            "[contenteditable]",
            # 传统输入框
            "textarea[placeholder*='检索']",
            "textarea[placeholder*='搜索']",
            "textarea[placeholder*='技术']",
            "textarea[placeholder*='输入']",
            "textarea",
            "input[type='text'][placeholder*='检索']",
            "input[type='text']",
            # role=textbox
            "[role='textbox']",
        ]

        for s in candidates:
            try:
                el = await self.page.query_selector(s)
                if el and await el.is_visible():
                    box = await el.bounding_box()
                    if box and box["y"] < 100 and box["width"] < 100:
                        continue  # 跳过顶部小元素
                    if box and box["width"] > 100:
                        self._search_input_sel = s
                        return s
            except Exception:
                continue

        # 兜底：用 JS 找最大的可见可编辑区域
        try:
            sel_from_js = await self.page.evaluate("""() => {
                const candidates = document.querySelectorAll(
                    '[contenteditable="true"], textarea, input[type="text"], [role="textbox"]');
                let best = null, bestArea = 0;
                for (const el of candidates) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 100 && r.height > 20 && r.y < 800) {
                        const area = r.width * r.height;
                        if (area > bestArea) {
                            bestArea = area;
                            best = el;
                        }
                    }
                }
                if (best) {
                    if (best.id) return '#' + best.id;
                    if (best.className && typeof best.className === 'string') {
                        const cls = best.className.trim().split(/\\s+/)[0];
                        if (cls) return '.' + cls;
                    }
                    return best.tagName.toLowerCase();
                }
                return '';
            }""")
            if sel_from_js:
                self._search_input_sel = sel_from_js
                return sel_from_js
        except Exception:
            pass

        self._search_input_sel = "[contenteditable]"
        return "[contenteditable]"

    async def _fill_search_input(self, selector: str, text: str):
        """
        向搜索框填入文本。对 contenteditable div 使用 fill()，
        对普通 input/textarea 使用 human_type 逐字输入。
        关键在于：必须触发 HimmPat 的 input 事件让搜索按钮变为可用。
        """
        try:
            # 先尝试 Playwright 的 fill（对 contenteditable 元素也有效，
            # 它会先清空再填入，并触发正确的输入事件）
            locator = self.page.locator(selector).first
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")

            if tag in ("input", "textarea"):
                # 标准输入框：用 fill 最快最可靠
                await locator.fill(text)
            else:
                # contenteditable div：fill 可能不生效，
                # 手动：点击聚焦 → 全选 → 删除 → insert_text
                await locator.click()
                await asyncio.sleep(0.3)
                await self.page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await self.page.keyboard.press("Delete")
                await asyncio.sleep(0.1)
                await self.page.keyboard.insert_text(text)
                await asyncio.sleep(0.3)

            # 等待搜索按钮变为可用（最多等 5 秒）
            for _ in range(20):
                disabled = await self.page.evaluate("""() => {
                    const btn = document.querySelector(
                        'button.el-button--primary:has-text("检索"), '
                        + 'button.el-button--primary:has-text("搜索"), '
                        + 'button.search-btn:not([disabled])'
                    );
                    if (!btn) return true; // 找不到按钮
                    return btn.disabled || btn.classList.contains('is-disabled');
                }""")
                if not disabled:
                    return  # 按钮已可用
                await asyncio.sleep(0.25)

            # 超时：按钮仍不可用，尝试强制触发 input 事件
            await locator.evaluate("""el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
            await asyncio.sleep(0.5)

        except Exception as e:
            # 兜底：用 human_type 方式
            try:
                await self.human.human_type(self.page, selector, text)
            except Exception:
                pass

    async def _click_search_button(self):
        """点击搜索/检索按钮（等按钮变为可用后点击）"""
        # 先等按钮变为可用（最多等 5s）
        for _ in range(20):
            try:
                info = await self.page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if ((t === '检索' || t === '搜索')
                            && btn.offsetParent !== null
                            && btn.getBoundingClientRect().width > 30) {
                            return {
                                disabled: btn.disabled,
                                hasDisabledClass: btn.classList.contains('is-disabled'),
                                text: t,
                            };
                        }
                    }
                    return null;
                }""")
                if info and not info["disabled"] and not info["hasDisabledClass"]:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)

        # 尝试点击
        candidates = [
            "button.el-button--primary:not(.is-disabled):has-text('检索')",
            "button.el-button--primary:not(.is-disabled):has-text('搜索')",
            "button:not([disabled]):has-text('检索')",
            "button:not([disabled]):has-text('搜索')",
            "button.search-btn:not([disabled])",
            "[class*='search-btn']",
            "button[type='submit']:not([disabled])",
            ".el-button:has-text('检索')",
            ".el-button:has-text('搜索')",
            "button.el-button--primary",
        ]

        for s in candidates:
            try:
                btn = await self.page.query_selector(s)
                if btn and await btn.is_visible():
                    disabled = await btn.is_disabled()
                    has_class = await btn.evaluate(
                        "el => el.classList.contains('is-disabled')")
                    if disabled or has_class:
                        continue
                    box = await btn.bounding_box()
                    if box and box["width"] > 30:
                        await self.human.human_click(self.page, selector=s)
                        self._search_btn_sel = s
                        return
            except Exception:
                continue

        # 兜底1: JS 强制点击（移除 disabled 后点击）
        try:
            clicked = await self.page.evaluate("""() => {
                const all = document.querySelectorAll('button');
                for (const el of all) {
                    const t = el.textContent?.trim() || '';
                    if ((t === '检索' || t === '搜索') && el.offsetParent !== null) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 20 && r.width < 300) {
                            el.classList.remove('is-disabled');
                            el.disabled = false;
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                return
        except Exception:
            pass

        # 兜底2: Enter 键
        await self.page.keyboard.press("Enter")

    # ================================================================
    # Step 2: 等待结果 + 查找结果条目
    # ================================================================

    async def _wait_for_results(self, timeout: int = 20):
        """等待搜索结果加载"""
        for _ in range(timeout):
            await asyncio.sleep(1)
            # 检查页面是否有足够内容和专利号
            try:
                txt = await self.page.evaluate(
                    "document.body?.innerText?.slice(0,3000) || ''")
                # 有 CN 专利号 + 足够文本量 = 结果已加载
                if re.search(r'CN\d{4,}', txt) and len(txt) > 200:
                    await asyncio.sleep(1)  # 再等一下确保渲染完毕
                    return
            except Exception:
                pass
        # 超时也不报错，后续 _find_result_items 会处理

    async def _find_result_items(self) -> list[dict]:
        """
        在搜索结果页上找到所有专利结果条目。
        策略：获取页面全文，按 CN 专利号分段，从每段中提取标题和摘要。
        """
        items = await self.page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            const bodyText = (document.body?.innerText || '');

            // 按 CN 专利号分段
            const segments = bodyText.split(/\\n(?=\\d+\\nCN\\d)/);
            // 如果上面的 split 不生效，用正则直接找
            const cnRegex = /CN\\s*(\\d{4,}[A-Z]?)/g;
            let cnMatch;

            // 找到所有 CN 公开号的位置（排除申请号）
            // 公开号特征: CN + 数字 + 字母后缀 (A/B/U/Y等)
            // 申请号特征: CN + 纯数字(可能带.数字)
            const positions = [];
            while ((cnMatch = cnRegex.exec(bodyText)) !== null) {
                const fullPN = cnMatch[0].replace(/\\s/g, '');
                // 跳过纯数字的申请号（如 CN202610044689），只保留带字母后缀的公开号
                if (!/[A-Z]$/i.test(fullPN)) continue;
                if (!seen.has(fullPN)) {
                    seen.add(fullPN);
                    positions.push({
                        pn: fullPN,
                        index: cnMatch.index,
                    });
                }
            }

            // 对每个 CN 号，提取周围的文本
            for (let i = 0; i < positions.length; i++) {
                const pos = positions[i];
                const nextPos = i + 1 < positions.length
                    ? positions[i + 1].index
                    : bodyText.length;
                // 取从当前 CN 号到下一个 CN 号之间的文本
                const segment = bodyText.slice(pos.index, nextPos);

                // 提取标题：CN号/类型之后，"申请号"之前的含中文长文本
                let title = '';
                const titleMatch = segment.match(
                    /(?:实用新型|发明|外观设计|授权|审中|战略专利|高价值专利)\\s*\\n\\s*([^\\n]{10,200}?)\\s*\\n\\s*(?:申请号|申请（专利）号|专利申请号)/);
                if (titleMatch) {
                    title = titleMatch[1].trim();
                } else {
                    // 兜底：找第一个含中文的长行
                    const lines = segment.split(/\\n/);
                    for (const line of lines) {
                        const t = line.trim();
                        if (t.length >= 10 && t.length < 300
                            && /[\\u4e00-\\u9fff]/.test(t)
                            && !/^CN\\d/.test(t)
                            && !/^(申请|IPC|摘要|专利|发明|实用|外观|授权|审中|战略|高价值|申请人|专利权人|发明人|代理|地址|公开号|公开日|主分类)/.test(t)
                            && !/^[A-H]\\d{2}[A-Z]/.test(t)
                            && !/^\\d{4}[-.]\\d/.test(t)) {
                            title = t;
                            break;
                        }
                    }
                }

                // 提取摘要
                let abstract = '';
                const absMatch = segment.match(
                    /摘要\\s*[：:]?\\s*([\\s\\S]{30,600}?)(?:\\n\\s*(?:备注|B\\s*\\n|PDF|\\d+\\s*\\n|$))/);
                if (absMatch) {
                    abstract = absMatch[1].trim().slice(0, 500);
                } else {
                    // 兜底：取"摘要"后面的文本
                    const absIdx = segment.indexOf('摘要');
                    if (absIdx >= 0) {
                        const afterAbs = segment.slice(absIdx + 2).trim();
                        const endIdx = Math.min(600, afterAbs.length);
                        abstract = afterAbs.slice(0, endIdx).replace(/^[：:]\\s*/, '').trim();
                    }
                }

                // 提取 IPC
                let ipc = '';
                const ipcMatch = segment.match(/IPC[^\\n]*\\n\\s*([A-H]\\d{2}[A-Z]\\d{1,6}[^\\n]*)/);
                if (ipcMatch) ipc = ipcMatch[1].trim().slice(0, 30);

                // 提取申请人
                let applicant = '';
                const appMatch = segment.match(
                    /(?:申请人|专利权人|申请（专利）权人)[^\\n]*\\n\\s*([^\\n]{3,50}?)\\s*\\n/);
                if (appMatch) applicant = appMatch[1].trim();

                results.push({
                    publication_number: pos.pn,
                    title: title,
                    abstract: abstract,
                    ipc: ipc,
                    applicant: applicant,
                    hasLink: false,  // 将从搜索结果页提取，不点击
                    rowY: pos.index,  // 用文本位置代替Y坐标排序
                });
            }

            return results;
        }""")

        return items or []

    # ================================================================
    # Step 3: 点击专利 → 提取详情
    # ================================================================

    async def _click_and_extract_one(self, patent_number: str,
                                     title_hint: str = "",
                                     query_index: int = 1,
                                     global_index: int = 1) -> Optional[dict]:
        """
        点击专利标题打开详情页，提取内容。

        HimmPat 的 /list 页面中，专利标题不是 <a> 链接，
        而是 div/span 元素通过 JS 事件处理器响应点击。
        因此使用 Playwright 原生 text locator 来定位和点击。
        """
        new_page = None
        async def on_popup(page):
            nonlocal new_page
            new_page = page
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

        try:
            self.page.context.on("page", on_popup)
            title = title_hint.strip()
            clicked = False

            # === HimmPat 需要双击标题进入详情！===
            if title and len(title) > 5:
                try:
                    # 用 Playwright locator 找标题元素
                    loc = self.page.get_by_text(title, exact=False).first
                    if await loc.count() > 0:
                        # 先单击聚焦，再双击进入详情
                        await loc.click(force=True, timeout=3000)
                        await asyncio.sleep(0.3)
                        await loc.dblclick(force=True, timeout=5000)
                        clicked = True
                except Exception:
                    pass

            # === 策略2: 部分标题 + 双击 ===
            if not clicked and title and len(title) > 10:
                keywords = [k for k in title[:40].split() if len(k) > 3]
                for kw in keywords[:3]:
                    try:
                        loc = self.page.get_by_text(kw, exact=False).first
                        if await loc.count() > 0:
                            await loc.click(force=True, timeout=3000)
                            await asyncio.sleep(0.3)
                            await loc.dblclick(force=True, timeout=5000)
                            clicked = True
                            break
                    except Exception:
                        continue

            # === 策略3: JS 双击含标题的可见元素 ===
            if not clicked:
                clicked = await self.page.evaluate(f"""(title) => {{
                    const all = document.querySelectorAll('div, span, td, h1, h2, h3, h4, p');
                    for (const el of all) {{
                        const t = (el.textContent || '').trim();
                        if (t === title || (t.length > 10 && t.includes(title.slice(0, 20)))) {{
                            const r = el.getBoundingClientRect();
                            if (r.width > 100 && r.height > 20 && el.offsetParent !== null) {{
                                el.dispatchEvent(new MouseEvent('dblclick', {{bubbles: true, cancelable: true}}));
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }}""", title)

            # === 策略4: 按专利号找并双击 ===
            if not clicked:
                short_pn = patent_number.replace(" ", "")[:8]
                try:
                    loc = self.page.get_by_text(short_pn, exact=False).first
                    if await loc.count() > 0:
                        await loc.dblclick(force=True, timeout=5000)
                        clicked = True
                except Exception:
                    pass

            if not clicked:
                return None

            # 等待详情页加载（可能在 SPA 内以面板/抽屉形式打开）
            await asyncio.sleep(3)

            # 关闭可能的弹窗/对话框（如"申请授权"弹窗）
            await self._dismiss_popups()

            if new_page:
                self._popup_page = new_page
                try:
                    await new_page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            else:
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

            await asyncio.sleep(1)

            # 提取详情
            detail = await self._extract_detail_page()
            detail["publication_number"] = patent_number
            if title and len(title) > len(detail.get("title", "")):
                detail["title"] = title

            return detail

        except Exception:
            return None
        finally:
            try:
                self.page.context.remove_listener("page", on_popup)
            except Exception:
                pass

    async def _extract_detail_page(self) -> dict:
        """从专利详情页提取全部内容"""
        target = self._popup_page if self._popup_page else self.page
        await asyncio.sleep(0.5)

        # 获取全文
        full_text = await target.evaluate(
            "document.body?.innerText?.trim() || ''"
        )

        # 结构化提取
        extracted = await target.evaluate("""() => {
            const body = document.body;
            const allText = body?.innerText || '';

            // 提取标题
            let title = '';
            const h1 = document.querySelector('h1');
            if (h1) title = h1.textContent.trim();
            if (!title) {
                const h2 = document.querySelector('h2');
                if (h2) title = h2.textContent.trim();
            }
            if (!title) {
                // 找页面中第一个看起来像标题的大字
                const allEls = document.querySelectorAll('h1, h2, h3, [class*="title"], [class*="Title"]');
                for (const el of allEls) {
                    const t = el.textContent.trim();
                    if (t.length > 5 && t.length < 300) {
                        title = t;
                        break;
                    }
                }
            }

            // 提取权利要求
            let claims = '';
            const claimEls = document.querySelectorAll(
                '[class*="claim"], [class*="Claim"], [id*="claim"], [id*="Claim"], '
                + '[class*="claims"], #claims'
            );
            for (const el of claimEls) {
                claims += el.textContent.trim() + '\\n';
            }
            if (!claims) {
                // 从全文中提取权利要求部分
                const m = allText.match(
                    /(?:权\\s*利\\s*要\\s*求\\s*书|CLAIMS?)\\s*\\n([\\s\\S]*?)(?=\\n\\s*(?:说\\s*明\\s*书|DESCRIPTION|附\\s*图|技术领域|TECHNICAL\\s*FIELD)|$)/
                );
                if (m) claims = m[1].trim();
            }

            // 提取说明书/描述
            let description = '';
            const descEls = document.querySelectorAll(
                '[class*="description"], [class*="Description"], '
                + '[class*="detail"], [class*="specification"], '
                + '[class*="abstract"], #description, #specification'
            );
            for (const el of descEls) {
                description += el.textContent.trim() + '\\n';
            }
            if (!description) {
                const m = allText.match(
                    /(?:说\\s*明\\s*书|DESCRIPTION|技术领域|TECHNICAL\\s*FIELD)[\\s\\S]*/
                );
                if (m) description = m[0].trim();
            }

            // 提取摘要
            let abstract = '';
            const absEl = document.querySelector('[class*="abstract"], [class*="Abstract"], #abstract');
            if (absEl) abstract = absEl.textContent.trim();
            if (!abstract) {
                const m = allText.match(
                    /(?:摘\\s*要|ABSTRACT)\\s*[：:]\\s*([\\s\\S]{50,800}?)(?=\\n\\s*(?:CN|申请|Applicat|权|Claim|\\d{8,}))/i
                );
                if (m) abstract = m[1].trim();
            }

            // 提取 IPC 分类号
            let ipc = '';
            const ipcMatch = allText.match(
                /(?:IPC|Int\\.?\\s*Cl\\.?|分类号)\\s*[：:]\\s*([A-H]\\d{2}[A-Z]\\d{1,6}(?:/[A-Z]\\d{2,})?)/i
            );
            if (ipcMatch) ipc = ipcMatch[1];

            // 提取申请人
            let applicant = '';
            const appMatch = allText.match(
                /(?:申请人|专利权人|Applicant|Patentee)\\s*[：:]\\s*(.{5,100}?)(?:\\n|$)/i
            );
            if (appMatch) applicant = appMatch[1].trim();

            // 提取发明人
            let inventor = '';
            const invMatch = allText.match(
                /(?:发明人|Inventor)\\s*[：:]\\s*(.{5,100}?)(?:\\n|$)/i
            );
            if (invMatch) inventor = invMatch[1].trim();

            // 提取公开日期
            let pubDate = '';
            const dateMatch = allText.match(
                /(?:公开日|申请日|公开\\s*\\(\\s*公告\\s*\\)\\s*日|Publication\\s*Date|Pub\\.?\\s*Date)\\s*[：:]\\s*(\\d{4}[-./年]\\d{1,2}[-./月]\\d{1,2})/i
            );
            if (dateMatch) pubDate = dateMatch[1].trim();

            return {
                title, claims: claims.slice(0, 30000),
                description: description.slice(0, 50000),
                abstract: abstract.slice(0, 3000),
                ipc, applicant, inventor,
                publication_date: pubDate
            };
        }""") or {}

        result = {
            "full_text": full_text[:80000],
            "title": extracted.get("title", ""),
            "claims": extracted.get("claims", ""),
            "description": extracted.get("description", ""),
            "abstract": extracted.get("abstract", ""),
            "ipc": extracted.get("ipc", ""),
            "applicant": extracted.get("applicant", ""),
            "inventor": extracted.get("inventor", ""),
            "publication_date": extracted.get("publication_date", ""),
        }

        return result

    # ================================================================
    # 关闭弹窗 / 对话框
    # ================================================================

    async def _dismiss_popups(self):
        """关闭页面上可能遮挡内容的弹窗/对话框"""
        try:
            dismissed = await self.page.evaluate("""() => {
                let count = 0;
                // Element UI / Ant Design 弹窗关闭按钮
                const closeSelectors = [
                    '.el-message-box__close',
                    '.el-dialog__close',
                    '.el-drawer__close',
                    '.el-message-box__btns button:has-text("确定")',
                    '.el-message-box__btns button:has-text("取消")',
                    '.el-message-box__btns button:has-text("关闭")',
                    '.el-dialog__footer button:has-text("确定")',
                    '.el-dialog__footer button:has-text("取消")',
                    '.ant-modal-close',
                    '[aria-label="Close"]',
                    '[aria-label="close"]',
                    '.el-icon-close',
                    '.el-message .el-icon-close',
                    'button.el-button:has-text("确定")',
                    'button.el-button:has-text("知道了")',
                ];
                for (const sel of closeSelectors) {
                    try {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            el.click();
                            count++;
                        }
                    } catch(e) {}
                }

                // 关闭所有 visible 的 el-dialog__wrapper / el-message-box__wrapper
                const wrappers = document.querySelectorAll(
                    '.el-dialog__wrapper, .el-message-box__wrapper, '
                    + '.el-overlay, .el-drawer__wrapper, '
                    + '.ant-modal-wrap');
                for (const w of wrappers) {
                    if (w.style.display !== 'none' && w.offsetParent !== null) {
                        const closeBtn = w.querySelector(
                            '[class*="close"], [class*="Close"], '
                            + 'button:has-text("确定"), button:has-text("取消")');
                        if (closeBtn) {
                            closeBtn.click();
                            count++;
                        }
                    }
                }
                return count;
            }""")
            if dismissed:
                await asyncio.sleep(1)
        except Exception:
            pass

    # ================================================================
    # 返回结果列表
    # ================================================================

    async def _go_back_to_results(self):
        """从详情页返回结果列表页（优先用保存的 /list URL）"""
        try:
            # 情况1: 新标签页 → 关闭标签页
            if self._popup_page:
                try:
                    await self._popup_page.close()
                except Exception:
                    pass
                self._popup_page = None
                await asyncio.sleep(0.5)
                return

            # 情况2: SPA 弹窗/侧边栏 → 尝试关闭
            try:
                closed = await self.page.evaluate("""() => {
                    const closeBtns = document.querySelectorAll(
                        '.el-dialog__close, .el-drawer__close, '
                        + '[class*="close"]:not([class*="search"]), '
                        + '.el-icon-close, [aria-label="Close"], '
                        + 'button:has-text("关闭"), button:has-text("返回"), '
                        + '[class*="back"]:not([class*="feed"])'
                    );
                    for (const btn of closeBtns) {
                        if (btn.offsetParent !== null) {
                            const r = btn.getBoundingClientRect();
                            if (r.width < 200 && r.height < 100) {
                                btn.click();
                                return 'closed';
                            }
                        }
                    }
                    return '';
                }""")
                if closed:
                    await asyncio.sleep(1)
                    return
            except Exception:
                pass

            # 情况3: 直接用保存的结果页 URL 导航回去
            if self._results_url and self._results_url != self.page.url:
                try:
                    await self.page.goto(
                        self._results_url,
                        wait_until="domcontentloaded", timeout=15000
                    )
                    await asyncio.sleep(2)
                    return
                except Exception:
                    pass

            # 情况4: 浏览器后退
            try:
                await self.page.go_back(wait_until="domcontentloaded", timeout=10000)
                await asyncio.sleep(2)
            except Exception:
                pass

        except Exception:
            pass

    # ================================================================
    # Step 5: 翻页
    # ================================================================

    async def _go_to_next_page(self) -> bool:
        """翻到下一页，返回 True 表示成功"""
        try:
            clicked = await self.page.evaluate("""() => {
                // Element UI 分页
                const nextBtn = document.querySelector(
                    '.el-pagination button.btn-next:not([disabled]), '
                    + '.el-pager li.active + li, '
                    + '.el-pagination .el-icon-arrow-right'
                );
                if (nextBtn && nextBtn.offsetParent !== null) {
                    nextBtn.click();
                    return 'el-pagination';
                }

                // 通用"下一页"按钮
                const nextTexts = document.querySelectorAll('button, a, li, span');
                for (const el of nextTexts) {
                    const t = (el.textContent || '').trim();
                    if ((t === '>' || t === '›' || t === '»'
                         || t === '下一页' || t === '下一頁' || t === 'Next')
                        && el.offsetParent !== null) {
                        el.click();
                        return 'text-button';
                    }
                }

                // Ant Design 分页
                const antNext = document.querySelector(
                    '.ant-pagination-next:not([disabled]), '
                    + '.ant-pagination-item-active + .ant-pagination-item'
                );
                if (antNext && antNext.offsetParent !== null) {
                    antNext.click();
                    return 'ant-pagination';
                }

                // 检查是否已是最后一页
                const disabledNext = document.querySelector(
                    '.btn-next[disabled], .el-icon-arrow-right[disabled], '
                    + '[class*="next"][disabled], '
                    + '[class*="pagination"] [class*="disabled"][class*="next"]'
                );
                if (disabledNext) return '';

                return '';
            }""")

            if clicked:
                await asyncio.sleep(2)  # 等新页加载
                return True
            return False
        except Exception:
            return False

    # ================================================================
    # 批量处理多条检索式
    # ================================================================

    async def execute_all_queries(self, queries: list[dict],
                                  max_per_query: int = 50,
                                  signals=None) -> list[list[dict]]:
        """
        执行全部检索式，每条返回 enriched results。
        """
        all_results = []
        for idx, query in enumerate(queries):
            if signals:
                signals.log.emit("INFO",
                    f"🔍 检索式 {idx+1}/{len(queries)}: "
                    f"{query.get('query_string', '')}")

            results = await self.execute_query(
                query.get("query_string", ""),
                query_index=idx + 1,
                max_results=max_per_query,
                signals=signals,
            )
            all_results.append(results)

            if signals:
                signals.log.emit("SUCCESS",
                    f"检索式{idx+1}完成: 获取 {len(results)} 篇专利详情")

            # 非最后一条时等待
            if idx < len(queries) - 1:
                await self.human.inter_search_delay(idx + 1)

        return all_results
