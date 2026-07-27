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
            await self.page.goto(detail_url, timeout=30000, wait_until="domcontentloaded")
            try:
                await self.page.wait_for_selector("h1", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)
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
                # 保留摘要中的公布号，不被详情页覆盖
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

    async def fetch_details_parallel(self, patents: list[dict], output_dir,
                                      concurrency: int = 5, signals=None) -> int:
        """并行抓取完整详情，每篇写入独立 JSON 到 output_dir。

        Args:
            patents: 搜索阶段的结果列表，每项含 doc_id, publication_number 等
            output_dir: 输出目录
            concurrency: 并发数

        已存在的文件自动跳过（断点续传）。返回成功抓取的数量。
        """
        import json as json_module
        from pathlib import Path

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # 构建 doc_id → 原始元数据的映射，用于保留正确的公布号
        meta_map = {}
        for p in patents:
            did = p.get("doc_id") or p.get("publication_number", "")
            if did:
                meta_map[did] = p

        doc_ids = list(meta_map.keys())

        # 断点续传：跳过已存在的文件
        remaining = []
        skipped = 0
        for did in doc_ids:
            safe = _safe_filename(did)
            fpath = out / f"{safe}.json"
            if fpath.exists():
                try:
                    existing = json_module.loads(fpath.read_text(encoding="utf-8"))
                    if existing.get("fetch_status") == "ok":
                        skipped += 1
                        continue
                except Exception:
                    pass
            remaining.append(did)

        if signals:
            signals.log.emit("INFO",
                f"  并行抓取: 共 {len(doc_ids)} 篇, "
                f"已缓存 {skipped} 篇, 待抓取 {len(remaining)} 篇 "
                f"(并发 {concurrency})")

        if not remaining:
            if signals:
                signals.log.emit("SUCCESS", "  全部已缓存，无需抓取")
            return len(doc_ids)

        sem = asyncio.Semaphore(concurrency)
        success_count = 0
        fail_count = 0

        async def _fetch_one(doc_id: str):
            nonlocal success_count, fail_count
            async with sem:
                # 随机延迟 1-3 秒，避免同时涌入触发限流
                await asyncio.sleep(random.uniform(1.0, 3.0))
                pg = await self.page.context.new_page()
                try:
                    detail_url = (
                        f"https://patentscope2.wipo.int/search/zh/detail.jsf"
                        f"?docId={doc_id}"
                    )
                    await pg.goto(detail_url, timeout=30000,
                                  wait_until="domcontentloaded")
                    try:
                        await pg.wait_for_selector("h1", timeout=10000)
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    detail = await self._extract_detail_page(doc_id, page=pg)
                    detail["fetch_status"] = "ok"

                    # 用搜索阶段的正确公布号覆盖，并用公布号命名文件
                    meta = meta_map.get(doc_id, {})
                    pub_from_search = meta.get("publication_number", "")
                    if pub_from_search:
                        detail["publication_number"] = pub_from_search
                        detail["patent_number"] = pub_from_search
                        file_key = pub_from_search
                    else:
                        file_key = doc_id

                    safe = _safe_filename(file_key)
                    fpath = out / f"{safe}.json"
                    fpath.write_text(
                        json_module.dumps(detail, indent=2,
                                          ensure_ascii=False, default=str),
                        encoding="utf-8")
                    success_count += 1
                    if signals:
                        signals.log.emit("INFO",
                            f"    ✓ [{success_count+fail_count}/{len(remaining)}] "
                            f"{detail.get('publication_number', doc_id)}")
                except Exception as e:
                    fail_count += 1
                    safe = _safe_filename(doc_id)
                    fpath = out / f"{safe}.json"
                    fpath.write_text(
                        json_module.dumps({
                            "doc_id": doc_id,
                            "fetch_status": "failed",
                            "error": str(e)[:200],
                        }, indent=2, ensure_ascii=False),
                        encoding="utf-8")
                    if signals:
                        signals.log.emit("WARN",
                            f"    ✗ [{success_count+fail_count}/{len(remaining)}] "
                            f"{doc_id}: {str(e)[:80]}")
                finally:
                    await pg.close()
                    # 请求间微小延迟，避免触发限流
                    await asyncio.sleep(random.uniform(0.3, 0.8))

        await asyncio.gather(
            *[_fetch_one(did) for did in remaining],
            return_exceptions=True)

        total_ok = skipped + success_count
        if signals:
            signals.log.emit("SUCCESS",
                f"  并行抓取完成: {total_ok}/{len(doc_ids)} 篇 "
                f"(新增 {success_count}, 缓存 {skipped}, 失败 {fail_count})")
        return total_ok

    # ================================================================
    # 搜索表单
    # ================================================================

    async def _navigate_and_search(self, query: str, signals=None):
        if signals:
            signals.log.emit("INFO", "  正在访问 PATENTSCOPE...")
        search_url = self.settings.patentscope_search_url
        # networkidle 确保 JSF 页面完全加载，不会中途跳转
        await self.page.goto(search_url, timeout=30000, wait_until="networkidle")

        if signals:
            signals.log.emit("INFO", "  正在提交检索式...")
        await self._fill_and_submit(query)

        if signals:
            signals.log.emit("INFO", "  等待搜索结果...")
        # 先等 URL 跳到结果页（快），再等结果表格出现
        try:
            await self.page.wait_for_url("**/result.jsf*", timeout=30000)
        except Exception:
            pass
        try:
            await self.page.wait_for_selector(
                ".ps-patent-result, .trans-result-list-row, table.patent-result-list",
                timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1)
        if signals:
            signals.log.emit("INFO", "  搜索结果已返回")

    async def _set_max_page_size(self, signals=None):
        """切到每页最多条数（200）。

        页面布局: 「相关性」<select> | 「每页:」<select> | 「查看:」
        找到包含选项"200"的 select 即目标。
        """
        try:
            all_selects = await self.page.locator("select").all()
            for loc in all_selects:
                try:
                    options = await loc.locator("option").all()
                    vals = []
                    for opt in options:
                        try:
                            v_text = (await opt.text_content()).strip()
                            v_num = int(v_text) if v_text.isdigit() else 0
                            if v_num > 0:
                                vals.append(v_num)
                        except Exception:
                            continue
                    # 包含 200 就是分页下拉框
                    if 200 in vals:
                        await loc.select_option(value="200")
                        if signals:
                            signals.log.emit("INFO", "  切换每页条数: 200 条")
                        await asyncio.sleep(3)
                        try:
                            await self.page.wait_for_selector(
                                ".ps-patent-result, .trans-result-list-row",
                                timeout=10000)
                        except Exception:
                            pass
                        return
                except Exception:
                    continue

            if signals:
                signals.log.emit("WARN", "  未找到包含200的分页下拉框")
        except Exception:
            if signals:
                signals.log.emit("WARN", "  未找到分页下拉框")

    async def _fill_and_submit(self, query: str):
        # 步骤1: 使用 Playwright locator 填入检索式（自动等待元素、防导航中断）
        try:
            input_locator = self.page.locator("#simpleSearchForm\\:fpSearch\\:input").first
            await input_locator.wait_for(state="visible", timeout=10000)
            await input_locator.fill(query)
            await asyncio.sleep(0.5)
        except Exception as e:
            raise RuntimeError(f"搜索框填入失败: {e}")

        # 步骤2: 点击搜索按钮（Playwright 自动等待并处理导航）
        try:
            btn_locator = self.page.locator("button[id*='fpSearch']").first
            await btn_locator.click()
        except Exception as e:
            raise RuntimeError(f"搜索提交失败: {e}")

    # ================================================================
    # 结果页解析
    # ================================================================

    async def _wait_for_results(self, timeout: int = 10) -> bool:
        """等待搜索结果表格出现，超时则假定无结果"""
        try:
            await self.page.wait_for_selector(
                ".ps-patent-result, .trans-result-list-row, table.patent-result-list",
                timeout=timeout * 1000)
            return True
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

    async def _extract_detail_page(self, doc_id: str, page=None) -> dict:
        pg = page or self.page
        result = {
            "publication_number": "", "title": "", "abstract": "",
            "claims": "", "description": "", "ipc": "",
            "applicant": "", "inventor": "", "publication_date": "",
            "application_number": "", "full_text": "",
        }

        # 书目数据
        biblio = await pg.evaluate('''() => {
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
        people = await pg.evaluate('''() => {
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
        claims_text = await self._click_and_extract_tab("PCTCLAIMS", page=pg)
        desc_text = await self._click_and_extract_tab("PCTDESCRIPTION", page=pg)

        if claims_text:
            claims_text = clean_patent_html_text(claims_text)
        if desc_text:
            desc_text = clean_patent_html_text(desc_text)

        # WO 等非 CN 专利没有独立 Claims/Description 标签，需从「全文」tab 提取
        if not claims_text or not desc_text:
            fulltext = await self._click_and_extract_tab("FULLTEXT", page=pg)
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
        """翻到下一页。先尝试 CSS 选择器，再尝试 JS 直接点击。"""
        try:
            # 多语言/多版本选择器：英文 + 中文 + PrimeFaces + 新版 PATENTSCOPE
            next_btn = self.page.locator(
                "a[id*='nextPage'], a[id*='navigationNext'], "
                "a:has-text('Next'), a:has-text('下一页'), "
                "a:has-text('›'), a:has-text('>'), a:has-text('»'), "
                "a.paginator-next, a.pagination-next, "
                "a[class*='ps-paginator']:has-text('>'), "
                ".ui-paginator-next:not(.ui-state-disabled)"
            ).first
            if await next_btn.count() > 0:
                cls = (await next_btn.get_attribute("class")) or ""
                style = (await next_btn.get_attribute("style")) or ""
                aria_disabled = (await next_btn.get_attribute("aria-disabled")) or ""
                if "disabled" in cls or "disabled" in aria_disabled or "display: none" in style:
                    return False
                await next_btn.click()
                await asyncio.sleep(2)
                try:
                    await self.page.wait_for_selector(
                        ".ps-patent-result, .trans-result-list-row",
                        timeout=10000)
                except Exception:
                    pass
                self._results_url = self.page.url
                return True
        except Exception:
            pass

        # 选择器兜底：用 JS 查找并点击下一页
        try:
            clicked = await self.page.evaluate('''() => {
                // CSS 选择器查找
                var selectors = [
                    "a[id*='nextPage']", "a[id*='navigationNext']",
                    "a.paginator-next", "a.pagination-next",
                    "a[class*='ps-paginator']",
                    ".ui-paginator-next:not(.ui-state-disabled)",
                    "a[aria-label*='Next']", "a[aria-label*='next']",
                    "button[aria-label*='Next']", "button[aria-label*='next']",
                ];
                for (var s = 0; s < selectors.length; s++) {
                    try {
                        var el = document.querySelector(selectors[s]);
                        if (el && el.offsetParent !== null) {
                            el.click();
                            return true;
                        }
                    } catch(e) {}
                }
                // 文本匹配：遍历所有 a 和 button 标签
                var elems = document.querySelectorAll("a, button");
                for (var i = 0; i < elems.length; i++) {
                    var t = (elems[i].textContent || "").trim();
                    var tag = elems[i].tagName;
                    // 精确匹配下一页文本
                    if (t === "Next" || t === ">" || t === ">>" ||
                        t === "›" || t === "»") {
                        if (elems[i].offsetParent !== null) {
                            elems[i].click();
                            return true;
                        }
                    }
                }
                return false;
            }''')
            if clicked:
                await asyncio.sleep(2)
                try:
                    await self.page.wait_for_selector(
                        ".ps-patent-result, .trans-result-list-row",
                        timeout=10000)
                except Exception:
                    pass
                self._results_url = self.page.url
                return True
        except Exception:
            pass
        return False

    # ── 辅助：点击标签页提取文本 ─────────────────────────────────────

    async def _click_and_extract_tab(self, href_keyword: str, page=None) -> str:
        """点击含有关键词的标签页并提取可见面板文本。"""
        pg = page or self.page
        try:
            tab = pg.locator(f"a[href*='{href_keyword}']").first
            if await tab.count() == 0:
                return ""
            await tab.click()
            await asyncio.sleep(2)
            text = await pg.evaluate('''() => {
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

def _safe_filename(doc_id: str) -> str:
    """将 doc_id 转为安全的文件名, 替换斜杠、冒号等非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', '_', doc_id)


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
