"""
PATENTSCOPE 集成爬虫 — 两阶段检索。
"""
import asyncio
import re
import random
from typing import Optional

from src.utils.config import Settings
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
            await self.page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(1.5)
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
                merged = {**p, **{k: v for k, v in detail.items() if v}}
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
            "publication_number": doc_id, "title": "", "abstract": "",
            "claims": "", "description": "", "ipc": "",
            "applicant": "", "inventor": "", "publication_date": "",
            "application_number": "", "full_text": "",
        }

        # 书目数据
        biblio = await self.page.evaluate('''() => {
            var data = {};
            var body = document.body.innerText || "";
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

        # 点击 Claims tab
        try:
            claims_tab = self.page.locator("a[href*='PCTCLAIMS']").first
            if await claims_tab.count() > 0:
                await claims_tab.click()
                await asyncio.sleep(2)
                claims_text = await self.page.evaluate('''() => {
                    var panels = document.querySelectorAll(".ui-tabs-panel");
                    for (var i = 0; i < panels.length; i++) {
                        if (panels[i].style.display !== "none" && panels[i].offsetParent) {
                            return panels[i].textContent.trim();
                        }
                    }
                    return "";
                }''')
                if claims_text:
                    claims_text = re.sub(r'Machine translation[\s\S]*?\[ZH \]\s*', '', claims_text)
                    claims_text = re.sub(r'\n{3,}', '\n\n', claims_text)
                result["claims"] = claims_text[:10000] if claims_text else ""
        except Exception:
            pass

        # 点击 Description tab
        try:
            desc_tab = self.page.locator("a[href*='PCTDESCRIPTION']").first
            if await desc_tab.count() > 0:
                await desc_tab.click()
                await asyncio.sleep(2)
                desc_text = await self.page.evaluate('''() => {
                    var panels = document.querySelectorAll(".ui-tabs-panel");
                    for (var i = 0; i < panels.length; i++) {
                        if (panels[i].style.display !== "none" && panels[i].offsetParent) {
                            return panels[i].textContent.trim();
                        }
                    }
                    return "";
                }''')
                result["description"] = desc_text[:20000] if desc_text else ""
        except Exception:
            pass

        # 纯摘要
        ft = result.get("full_text", "")
        if ft:
            m = re.search(r'Abstract\n\(EN\)\s*(.*?)(?:\n\n\(ZH\)|\n\n#)', ft, re.DOTALL)
            if m:
                result["abstract"] = m.group(1).strip()[:5000]
            else:
                m = re.search(r'Abstract[\s\S]*?(?=Claims|Description|$)', ft)
                if m:
                    result["abstract"] = m.group(0).replace("Abstract", "").strip()[:5000]

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
