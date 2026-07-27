"""
PATENTSCOPE 集成爬虫 — 两阶段检索。
"""
import asyncio
import re
import random
from typing import Optional

from src.utils.config import Settings
from src.utils.text_cleaner import clean_patent_html_text
from src.web_automation.human_behavior import HumanBehavior


class PatentscopeScraper:
    """PATENTSCOPE 两阶段爬虫"""

    def __init__(self, page, settings: Settings, human: HumanBehavior):
        self.page = page
        self.settings = settings
        self.human = human
        self._results_url = None

    # ================================================================
    # 阶段1: 搜索 + 摘要解析
    # ================================================================

    async def search_abstracts(self, query: str, max_results: int = 200,
                                signals=None) -> list[dict]:
        all_items = []
        await self._navigate_and_search(query, signals)
        self._results_url = self.page.url
        # 切换每页 200 条
        await self._set_max_page_size(signals)

        page_num = 1
        while len(all_items) < max_results:
            if signals:
                signals.progress.emit(
                    int(10 + len(all_items) / max_results * 20) if max_results else 30,
                    f"解析结果页 {len(all_items)}/{max_results}")

            await self._wait_for_results()
            page_items = await self._parse_results_table()

            if not page_items:
                if signals:
                    signals.log.emit("WARN", f"  第{page_num}页未解析到结果")
                break

            remaining = max_results - len(all_items)
            all_items.extend(page_items[:remaining])

            if signals:
                signals.log.emit("INFO",
                    f"  结果页解析: {len(page_items)} 条, 累计 {len(all_items)}/{max_results}")

            if len(all_items) >= max_results:
                break

            if signals:
                signals.log.emit("INFO", "  翻到下一页...")
            has_next = await self._go_to_next_page()
            if not has_next:
                break
            page_num += 1

        if signals:
            signals.log.emit("SUCCESS", f"  摘要检索完成: {len(all_items)} 篇")
        return all_items

    # ================================================================
    # 阶段2: 按需抓取详情
    # ================================================================

    async def fetch_detail(self, doc_id: str) -> Optional[dict]:
        detail_url = f"https://patentscope2.wipo.int/search/zh/detail.jsf?docId={doc_id}"
        try:
            await self.page.goto(detail_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            return await self._extract_detail_page(doc_id)
        except Exception:
            return None

    async def fetch_details_batch(self, patents: list[dict], signals=None) -> list[dict]:
        enriched = []
        total = len(patents)
        for i, p in enumerate(patents):
            doc_id = p.get("doc_id", "")
            pub_num = p.get("publication_number", "?")
            if signals:
                signals.log.emit("INFO",
                    f"  获取详情 [{i+1}/{total}]: {pub_num} (每条约10-15秒)...")
            detail = await self.fetch_detail(doc_id)
            if detail:
                # 保留摘要中的公开号，不被详情页覆盖
                pub_num = p.get("publication_number", "")
                merged = {**p, **{k: v for k, v in detail.items() if v}}
                if pub_num:
                    merged["publication_number"] = pub_num
                enriched.append(merged)
            else:
                p["_no_detail"] = True
                enriched.append(p)
            await asyncio.sleep(0.5 + random.uniform(0, 0.5))

        if signals:
            full_count = sum(1 for r in enriched if not r.get("_no_detail"))
            signals.log.emit("SUCCESS", f"  详情获取完成: {full_count}/{total} 篇获取到全文")
        return enriched

    # ================================================================
    # 搜索表单
    # ================================================================

    async def _navigate_and_search(self, query: str, signals=None):
        if signals:
            signals.log.emit("INFO", "  正在访问 PATENTSCOPE...")
        search_url = self.settings.patentscope_search_url
        await self.page.goto(search_url, timeout=90000, wait_until="domcontentloaded")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(2)

        if signals:
            signals.log.emit("INFO", "  正在提交检索式...")
        await self._fill_and_submit(query)

        if signals:
            signals.log.emit("INFO", "  等待搜索结果（可能需要30-60秒）...")
        try:
            await self.page.wait_for_url("**/result.jsf*", timeout=90000)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(3)
        if signals:
            signals.log.emit("INFO", "  搜索结果已返回")

    async def _set_max_page_size(self, signals=None):
        """直接用 JS getElementById 设置每页200条"""
        try:
            result = await self.page.evaluate('''() => {
                var ids = [
                    "resultListCommandsForm:perPage:input",
                    "settingsForm:lengthOption:input",
                ];
                for (var i = 0; i < ids.length; i++) {
                    var el = document.getElementById(ids[i]);
                    if (!el || !el.options) continue;
                    var maxOpt = null, maxVal = null;
                    for (var j = 0; j < el.options.length; j++) {
                        var v = parseInt(el.options[j].value, 10);
                        if (!isNaN(v) && (!maxOpt || v > maxOpt)) {
                            maxOpt = v;
                            maxVal = el.options[j].value;
                        }
                    }
                    if (maxVal && maxOpt > parseInt(el.value, 10)) {
                        el.value = maxVal;
                        el.dispatchEvent(new Event("change", {bubbles: true}));
                        return "ok: " + el.value;
                    }
                }
                return "not found";
            }''')
            if signals:
                if result and result.startswith("ok"):
                    signals.log.emit("INFO", f"  切换每页条数: {result}")
            if result and result.startswith("ok"):
                await asyncio.sleep(2)
        except Exception:
            pass

    async def _fill_and_submit(self, query: str):
        try:
            await self.page.evaluate(f'''(query) => {{
                var input = document.getElementById("simpleSearchForm:fpSearch:input");
                if (!input) return;
                input.value = query;
                input.dispatchEvent(new Event("input", {{bubbles: true}}));
                input.dispatchEvent(new Event("change", {{bubbles: true}}));
                var buttons = document.querySelectorAll("button");
                for (var i = 0; i < buttons.length; i++) {{
                    if (buttons[i].id && buttons[i].id.indexOf("fpSearch") >= 0) {{
                        buttons[i].click();
                        return;
                    }}
                }}
                var form = document.getElementById("simpleSearchForm");
                if (form) form.submit();
            }}''', query)
        except Exception as e:
            raise RuntimeError(f"搜索提交失败: {e}")

    # ================================================================
    # 结果页解析
    # ================================================================

    async def _wait_for_results(self, timeout: int = 60) -> bool:
        try:
            # 同时检测"有结果"和"无结果"两种情况
            await self.page.wait_for_selector(
                ".ps-patent-result, .trans-result-list-row, "
                "table.patent-result-list, "
                ".no-results, .noResults, "
                ":has-text('没有找到符合'), :has-text('No results'), "
                ":has-text('no results found')",
                timeout=timeout * 1000)
            # 确认是不是无结果页
            no_results = await self.page.evaluate('''() => {
                var body = document.body.innerText || "";
                return body.includes("没有找到符合") || body.includes("No results") || body.includes("no results found");
            }''')
            return not no_results
        except Exception:
            return False

    async def _parse_results_table(self) -> list[dict]:
        items = await self.page.evaluate('''() => {
            var results = [];
            var rows = document.querySelectorAll("tr.trans-result-list-row");
            if (rows.length === 0) { rows = document.querySelectorAll(".ps-patent-result"); }
            rows.forEach(function(row) {
                var item = {};
                var numEl = row.querySelector(".ps-patent-result--title--patent-number");
                if (numEl) item.patent_number = numEl.textContent.trim();
                var titleEl = row.querySelector(".ps-patent-result--title--title");
                if (titleEl) item.title = titleEl.textContent.trim();
                var linkEl = row.querySelector("a[href*='detail']");
                if (linkEl) {
                    item.detail_url = linkEl.href;
                    var m = linkEl.href.match(/docId=([^&]+)/);
                    item.doc_id = m ? m[1] : "";
                    if (!item.patent_number) item.patent_number = linkEl.textContent.trim();
                }
                if (!item.doc_id) item.doc_id = item.patent_number || "";
                item.publication_number = item.patent_number || item.doc_id || "";

                var appEl = row.querySelector(".ps-patent-result--applicant");
                if (appEl) item.applicant = appEl.textContent.trim();
                var invEl = row.querySelector(".ps-patent-result--inventor");
                if (invEl) item.inventor = invEl.textContent.trim();
                var ipcEl = row.querySelector(".ps-patent-result--ipc");
                if (ipcEl) item.ipc = ipcEl.textContent.trim();
                var dateEl = row.querySelector(".ps-patent-result--title--ctr-pubdate");
                if (dateEl) item.publication_date = dateEl.textContent.trim();
                var absEl = row.querySelector(".ps-patent-result--abstract");
                if (absEl) item.abstract_snippet = absEl.textContent.trim();

                // 申请号
                var rowText = row.textContent || "";
                var appMatch = rowText.match(/申请号\s*([\d.X]+)/);
                if (appMatch) item.application_number = appMatch[1];

                if (item.publication_number || item.title) { results.push(item); }
            });
            return results;
        }''')
        return items

    # ================================================================
    # 详情页提取
    # ================================================================

    async def _extract_detail_page(self, doc_id: str) -> dict:
        result = {
            "publication_number": "", "title": "", "abstract": "",
            "claims": "", "description": "", "ipc": "",
            "applicant": "", "inventor": "", "publication_date": "",
            "application_number": "", "full_text": "",
        }

        # 书目数据
        biblio = await self.page.evaluate('''() => {
            var data = {};
            var body = (document.body && document.body.innerText) || "";
            data.full_text = body.substring(0, 80000);
            var h1 = document.querySelector("h1");
            if (h1) data.title = h1.textContent.trim();
            var lines = body.split(/\\n/);
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line === "申请号" || line === "Application Number") { data.app_number = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "公布号" || line === "Publication Number") { data.pub_number = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "公布日" || line === "Publication Date") { data.pub_date = lines[i+1] ? lines[i+1].trim() : ""; }
                if ((line === "IPC" || line === "国际专利分类") && lines[i+1]) {
                    var ipcLine = lines[i+1].trim();
                    if (ipcLine.match(/^[A-H]\\d/)) data.ipc = ipcLine;
                }
            }
            return data;
        }''')
        result["full_text"] = biblio.get("full_text", "")
        result["title"] = biblio.get("title", "")
        result["application_number"] = biblio.get("app_number", "")
        result["publication_date"] = biblio.get("pub_date", "")
        result["ipc"] = biblio.get("ipc", "")
        # 公布号：页面提取，纯数字时补 CN 前缀
        pub = (biblio.get("pub_number", "") or "").strip()
        if pub and pub.isdigit():
            pub = f"CN{pub}"
        result["publication_number"] = pub or doc_id
        result["patent_number"] = result["publication_number"]

        # 申请人/发明人
        people = await self.page.evaluate('''() => {
            var body = document.body.innerText || "";
            var lines = body.split(/\\n/);
            var result = {applicant: "", inventor: ""};
            var inA = false, inI = false;
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line === "申请人" || line === "Applicants") { inA = true; inI = false; continue; }
                if (line === "发明人" || line === "Inventors") { inI = true; inA = false; continue; }
                if (line === "代理人" || line === "Agents" || line === "标题" || line === "Title") { inA = inI = false; continue; }
                if (inA && line) result.applicant += line + "; ";
                if (inI && line) result.inventor += line + "; ";
            }
            return result;
        }''')
        result["applicant"] = people.get("applicant", "").strip().rstrip(";")
        result["inventor"] = people.get("inventor", "").strip().rstrip(";")

        # 点击 Claims / Description tab（CN 专利有效，WO 专利为空）
        claims_text = await self._click_and_extract_tab("PCTCLAIMS")
        desc_text = await self._click_and_extract_tab("PCTDESCRIPTION")

        if claims_text:
            claims_text = clean_patent_html_text(claims_text)
        if desc_text:
            desc_text = clean_patent_html_text(desc_text)

        # WO 等非 CN 专利没有独立 Claims/Description 标签，需从「全文」tab 提取
        if not claims_text or not desc_text:
            fulltext = await self._click_and_extract_tab("FULLTEXT")
            if fulltext:
                fulltext = clean_patent_html_text(fulltext)
                if not claims_text:
                    claims_text = _extract_wo_claims(fulltext)
                if not desc_text:
                    desc_text = _extract_wo_description(fulltext)

        if claims_text:
            result["claims"] = claims_text[:10000]
        if desc_text:
            result["description"] = desc_text[:20000]

        # 纯摘要 — 摘要/Abstract 双模式，中文优先
        ft = result.get("full_text", "")
        if ft:
            abstract = ""
            # 尝试中文摘要：摘要 ... (ZH) ...
            m = re.search(r'摘要[\s\S]*?\(ZH\)\s*(.*?)(?:\n\n#|\n相关专利|\n$)', ft, re.DOTALL)
            if m:
                abstract = m.group(1).strip()
            if not abstract:
                # 尝试英文摘要：Abstract\n(EN) ...
                m = re.search(r'Abstract\n\(EN\)\s*(.*?)(?:\n\n\(ZH\)|\n\n#)', ft, re.DOTALL)
                if m:
                    abstract = m.group(1).strip()
            if not abstract:
                # 降级：摘要 ... (EN) ... (WO/CN 中文界面)
                m = re.search(r'摘要[\s\S]*?\(EN\)\s*(.*?)(?:\n\n\(FR\)|\n\n\(ZH\)|\n\n#)', ft, re.DOTALL)
                if m:
                    abstract = m.group(1).strip()
            if not abstract:
                # 最后降级：任意 Abstract/摘要 开头内容
                m = re.search(r'(?:摘要|Abstract)\s*[\s\S]*?(?=Claims|Description|$)', ft)
                if m:
                    abstract = m.group(0)
                    abstract = re.sub(r'^(?:摘要|Abstract)\s*', '', abstract).strip()
            if abstract:
                result["abstract"] = abstract[:5000]

        return result

    # ================================================================
    # 导航
    # ================================================================

    async def _go_to_next_page(self) -> bool:
        try:
            next_btn = self.page.locator(
                "a[id*='nextPage'], a[id*='navigationNext'], "
                "a:has-text('Next'), .ui-paginator-next:not(.ui-state-disabled)"
            ).first
            if await next_btn.count() > 0:
                cls = await next_btn.get_attribute("class") or ""
                if "disabled" in cls:
                    return False
                await next_btn.click()
                await self.page.wait_for_load_state("networkidle", timeout=30000)
                await asyncio.sleep(2)
                self._results_url = self.page.url
                return True
        except Exception:
            pass
        return False

    # ── 辅助：点击标签页提取文本 ─────────────────────────────────────

    async def _click_and_extract_tab(self, href_keyword: str) -> str:
        """点击含有关键词的标签页并提取可见面板文本。"""
        try:
            tab = self.page.locator(f"a[href*='{href_keyword}']").first
            if await tab.count() == 0:
                return ""
            await tab.click()
            await asyncio.sleep(2)
            text = await self.page.evaluate('''() => {
                var panels = document.querySelectorAll(".ui-tabs-panel");
                for (var i = 0; i < panels.length; i++) {
                    if (panels[i].style.display !== "none" && panels[i].offsetParent) {
                        return panels[i].textContent.trim();
                    }
                }
                return "";
            }''')
            return text or ""
        except Exception:
            return ""


# ── WO 全文切分工具 ──────────────────────────────────────────────────────

def _extract_wo_claims(fulltext: str) -> str:
    """从 WO 全文文本中提取权利要求书正文。

    WO 全文有两次「权利要求书」：
    1. 导航区 — 仅编号（1 2 3...20），无正文
    2. 正文区 — [权利要求 1] ... 实际内容
    """
    idx = fulltext.rfind('权利要求书')
    if idx < 0:
        m = re.search(r'Claims?\s*\n\s*\[Claim\s*\d+\]', fulltext, re.IGNORECASE)
        idx = m.start() if m else -1
    if idx < 0:
        return ""
    tail = fulltext[idx:]
    m = re.search(r'\[权利要求\s*\d+\][\s\S]*', tail)
    if m:
        return m.group(0).strip()
    m = re.search(r'\[Claim\s*\d+\][\s\S]*', tail, re.IGNORECASE)
    return m.group(0).strip() if m else ""


def _extract_wo_description(fulltext: str) -> str:
    """从 WO 全文文本中提取说明书正文。

    「技术领域」只出现在正文区，用其定位最可靠。
    """
    m = re.search(r'(技术领域[\s\S]*?)(?:权利要求书\s*\[权利要求|$)', fulltext)
    if not m:
        m = re.search(r'(Technical\s*Field[\s\S]*)$', fulltext, re.IGNORECASE)
    if not m:
        m = re.search(r'(发明内容[\s\S]*)$', fulltext)
    return m.group(1).strip() if m else ""
