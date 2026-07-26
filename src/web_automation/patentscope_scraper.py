"""
PATENTSCOPE 集成爬虫 — 两阶段检索：
  阶段1: 搜索 + 解析结果页摘要（快，200条/页）
  阶段2: 按需抓取详情页全文（只对 AI 筛选后的 10-20 篇）

与 HimmPatScraper 的关键区别：
  1. 无需登录 — PATENTSCOPE 免费公开访问
  2. 传统 HTML 表单 — 标准 <input>/<textarea> + <a> 链接
  3. 摘要直接来自结果页 — 无需逐条点击
  4. 详情页按需获取 — 只对筛选后的专利拉全文
  5. 每页最多 200 条 — 通常单页即满足需求
"""
import asyncio
import re
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
    # 阶段1: 搜索 + 摘要解析（轻量级，不访问详情页）
    # ================================================================

    async def search_abstracts(self, query: str, max_results: int = 200,
                                signals=None) -> list[dict]:
        """
        执行检索，只解析结果页的摘要信息，不访问详情页。

        Returns:
            list[dict]: 每项包含 publication_number, doc_id, title,
                        abstract_snippet, ipc, applicant, inventor,
                        publication_date, detail_url
        """
        all_items = []

        # 导航 + 搜索 + 等待结果页
        await self._navigate_and_search(query, signals)
        self._results_url = self.page.url

        while len(all_items) < max_results:
            if signals:
                signals.progress.emit(
                    int(10 + len(all_items) / max_results * 20) if max_results else 30,
                    f"解析结果页 {len(all_items)}/{max_results}")

            await self._wait_for_results()
            page_items = await self._parse_results_table()

            if not page_items:
                break

            # 只取需要的数量（单页最多200）
            remaining = max_results - len(all_items)
            all_items.extend(page_items[:remaining])

            if signals:
                signals.log.emit("INFO",
                    f"  已获取 {len(all_items)}/{max_results} 条摘要")

            if len(all_items) >= max_results:
                break

            # 翻页
            has_next = await self._go_to_next_page()
            if not has_next:
                break

        if signals:
            signals.log.emit("SUCCESS",
                f"  摘要检索完成: {len(all_items)} 篇")

        return all_items

    # ================================================================
    # 阶段2: 按需抓取详情（只对筛选后的专利）
    # ================================================================

    async def fetch_detail(self, doc_id: str) -> Optional[dict]:
        """
        获取单篇专利的全文详情。

        Returns:
            dict: title, abstract, claims, description, full_text,
                  ipc, applicant, inventor, publication_date
        """
        detail_url = f"https://patentscope2.wipo.int/search/zh/detail.jsf?docId={doc_id}"
        try:
            await self.page.goto(detail_url, timeout=60000,
                                 wait_until="domcontentloaded")
            await self.page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(1.5)
            return await self._extract_detail_page(doc_id)
        except Exception:
            return None

    async def fetch_details_batch(self, patents: list[dict],
                                   signals=None) -> list[dict]:
        """
        批量获取多篇专利的全文详情。

        Args:
            patents: 阶段1 返回的摘要列表（需含 doc_id）

        Returns:
            list[dict]: 补充了 claims, description, full_text 的完整专利信息
        """
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
                # 合并：用详情页的数据补充/覆盖摘要
                merged = {**p, **{k: v for k, v in detail.items() if v}}
                enriched.append(merged)
            else:
                # 降级：保留摘要信息
                p["_no_detail"] = True
                enriched.append(p)

            # 速率限制
            import random
            await asyncio.sleep(0.5 + random.uniform(0, 0.5))

        if signals:
            full_count = sum(1 for r in enriched if not r.get("_no_detail"))
            signals.log.emit("SUCCESS",
                f"  详情获取完成: {full_count}/{total} 篇获取到全文")

        return enriched

    # ================================================================
    # 搜索表单操作
    # ================================================================

    async def _navigate_and_search(self, query: str, signals=None):
        """导航到搜索页并执行检索"""
        if signals:
            signals.log.emit("INFO", "  正在访问 PATENTSCOPE...")
        search_url = self.settings.patentscope_search_url
        await self.page.goto(search_url, timeout=90000,
                             wait_until="domcontentloaded")
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

    async def _fill_and_submit(self, query: str):
        """填入检索式并点击搜索按钮"""
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
    # 结果页解析（仅摘要）
    # ================================================================

    async def _wait_for_results(self, timeout: int = 60) -> bool:
        try:
            await self.page.wait_for_selector(
                ".ps-patent-result, .trans-result-list-row, table.patent-result-list",
                timeout=timeout * 1000)
            return True
        except Exception:
            return False

    async def _parse_results_table(self) -> list[dict]:
        """解析结果页 — 提取每个专利的摘要级信息"""
        items = await self.page.evaluate('''() => {
            var results = [];
            var rows = document.querySelectorAll("tr.trans-result-list-row");
            if (rows.length === 0) {
                rows = document.querySelectorAll(".ps-patent-result");
            }
            rows.forEach(function(row) {
                var item = {};

                var numEl = row.querySelector(".ps-patent-result--title--patent-number");
                if (numEl) item.patent_number = numEl.textContent.trim();

                var titleEl = row.querySelector(".ps-patent-result--title--title");
                if (titleEl) item.title = titleEl.textContent.trim();

                var linkEl = row.querySelector("a[href*='detail']");
                if (linkEl) {
                    item.detail_url = linkEl.href;
                    var docMatch = linkEl.href.match(/docId=([^&]+)/);
                    item.doc_id = docMatch ? docMatch[1] : "";
                    if (!item.patent_number) item.patent_number = linkEl.textContent.trim();
                }
                if (!item.patent_number) {
                    var recEl = row.querySelector(".ps-patent-result--title--record-number");
                    if (recEl) item.patent_number = recEl.textContent.trim();
                }
                if (!item.doc_id) item.doc_id = item.patent_number || "";

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

                // 申请号（结果页常显示在标题行附近）
                var appNumEl = row.querySelector(".ps-patent-result--title--application-number");
                if (!appNumEl) {
                    // 从整行文本中提取 "申请号 XXXXXX"
                    var rowText = row.textContent || "";
                    var appMatch = rowText.match(/申请号\s*([\d.X]+)/);
                    if (appMatch) item.application_number = appMatch[1];
                } else {
                    item.application_number = appNumEl.textContent.trim();
                }

                item.publication_number = item.patent_number || item.doc_id || "";

                if (item.publication_number || item.title) {
                    results.push(item);
                }
            });
            return results;
        }''')
        return items

    # ================================================================
    # 详情页提取（全文）
    # ================================================================

    async def _extract_detail_page(self, doc_id: str) -> dict:
        """从详情页提取专利结构化数据 — 逐 tab 点击提取"""
        result = {
            "publication_number": doc_id, "title": "",
            "abstract": "", "claims": "", "description": "",
            "ipc": "", "applicant": "", "inventor": "",
            "publication_date": "", "application_number": "",
            "full_text": "",
        }

        # 先提取 biblio 数据（当前可见的 National Biblio Data tab）
        biblio = await self.page.evaluate('''() => {
            var data = {};
            var body = document.body.innerText || "";
            data.full_text = body.substring(0, 80000);

            var h1 = document.querySelector("h1");
            if (h1) data.title = h1.textContent.trim();

            // 从页面文本中提取纯 biblio 字段（跳过导航和重复内容）
            var lines = body.split(/\\n/);
            var inBiblio = false;
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (!line) continue;
                if (line === "Office" || line === "局") { data.office = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "Application Number" || line === "申请号") { data.app_number = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "Application Date" || line === "申请日") { data.app_date = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "Publication Number" || line === "公布号") { data.pub_number = lines[i+1] ? lines[i+1].trim() : ""; }
                if (line === "Publication Date" || line === "公布日") { data.pub_date = lines[i+1] ? lines[i+1].trim() : ""; }
                if ((line === "IPC" || line === "国际专利分类") && lines[i+1]) {
                    // 只取第一行纯分类号
                    var ipcLine = lines[i+1].trim();
                    if (ipcLine.match(/^[A-H]\\d/)) data.ipc = ipcLine;
                }
            }
            return data;
        }''')

        result["full_text"] = biblio.get("full_text", "")
        result["title"] = biblio.get("title", "")
        result["publication_date"] = biblio.get("pub_date", "")
        result["application_number"] = biblio.get("app_number", "")
        result["ipc"] = biblio.get("ipc", "")

        # 提取申请人/发明人
        people = await self.page.evaluate('''() => {
            var body = document.body.innerText || "";
            var lines = body.split(/\\n/);
            var result = {applicant: "", inventor: ""};
            var inApplicants = false, inInventors = false;
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim();
                if (line === "Applicants") { inApplicants = true; continue; }
                if (line === "Inventors") { inInventors = true; inApplicants = false; continue; }
                if (line === "Agents" || line === "Title") { inApplicants = false; inInventors = false; continue; }
                if (inApplicants && line) result.applicant += line + "; ";
                if (inInventors && line) result.inventor += line + "; ";
            }
            return result;
        }''')
        result["applicant"] = people.get("applicant", "").strip().rstrip(";")
        result["inventor"] = people.get("inventor", "").strip().rstrip(";")

        # === 点击 Claims tab 获取权利要求 ===
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
                    claims_text = self._clean_claims_text(claims_text)
                result["claims"] = claims_text[:10000]
        except Exception:
            pass

        # === 点击 Description tab 获取说明书 ===
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

        # === 提取纯摘要（从 full_text 中取 Abstract 段） ===
        ft = result.get("full_text", "")
        if ft:
            import re
            # 英文摘要
            m = re.search(r'Abstract\n\(EN\)\s*(.*?)(?:\n\n\(ZH\)|\n\n#)', ft, re.DOTALL)
            if m:
                result["abstract"] = m.group(1).strip()[:5000]
            else:
                # 降级
                m = re.search(r'Abstract[\s\S]*?(?=Claims|Description|$)', ft)
                if m:
                    result["abstract"] = m.group(0).replace("Abstract", "").strip()[:5000]

        return result

    @staticmethod
    def _clean_claims_text(text: str) -> str:
        """清理 claims 文本，去掉导航、翻译UI、书目等垃圾"""
        import re
        # 去掉机器翻译选择器块
        text = re.sub(r'Machine translation[\s\S]*?\[ZH \]\s*', '', text)
        text = re.sub(r'Note: Text based on automatic Optical Character.*?legal matters\s*', '', text)
        text = re.sub(r'WIPO Translate[A-Za-z]+\s*', '', text)
        # 去掉导航标签行（单独的 Claims/Drawings/Documents/PermaLink）
        text = re.sub(r'^(Claims|Drawings|Documents|PermaLink)\s*$', '', text, flags=re.MULTILINE)
        # 去掉书目重复
        text = re.sub(r'Office\nChina[\s\S]*?(?=Claims|Claim|What is claimed|\d+\.\s)', '', text)
        # 去掉多余空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 去掉末尾标记
        text = re.sub(r'\n#\s*-\s*$', '', text)
        return text.strip()

    # ================================================================
    # 导航辅助
    # ================================================================

    async def _go_to_next_page(self) -> bool:
        """翻到下一页"""
        try:
            next_btn = self.page.locator(
                "a[id*='nextPage'], a[id*='navigationNext'], "
                "a:has-text('Next'), "
                ".ui-paginator-next:not(.ui-state-disabled)"
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
