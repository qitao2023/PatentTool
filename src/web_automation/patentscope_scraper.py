"""
PATENTSCOPE 集成爬虫 — 两阶段检索。
"""
import asyncio
import contextvars
import re
import random
from typing import Optional

from src.utils.config import Settings
from src.utils.text_cleaner import clean_patent_html_text
from src.web_automation.human_behavior import HumanBehavior
from src.web_automation.google_patents import fetch_patent_text


# 记录最近一次详情提取失败的具体原因，供外层日志使用。
# 用 ContextVar 而非实例属性：fetch_details_parallel 会并发跑多个 _fetch_one，
# 每个 asyncio task 有独立的 context，不会互相污染。
_EXTRACT_FAIL_REASON = contextvars.ContextVar(
    "patent_extract_fail_reason", default="")


class PatentscopeScraper:
    """PATENTSCOPE 两阶段爬虫"""

    def __init__(self, page, settings: Settings, human: HumanBehavior):
        self.page = page
        self.settings = settings
        self.human = human
        self._results_url = None
        self._total_from_page_text = 0
        self._no_results = False

    # ================================================================
    # 阶段1: 搜索 + 摘要解析
    # ================================================================

    async def search_abstracts(self, query: str, max_results: int = 200,
                                signals=None) -> list[dict]:
        all_items = []
        # 每次新搜索重置缓存的总结果数
        self._total_from_page_text = 0
        self._no_results = False
        await self._navigate_and_search(query, signals)
        self._results_url = self.page.url

        # 无结果 → 直接返回（检查前2000字符，避免页头导航撑大截断位置）
        body_text = await self.page.evaluate(
            "() => document.body?.innerText?.substring(0, 2000) || ''")
        if any(m in body_text for m in (
            "没有找到符合您检索的结果",
            "未找到结果", "No results found",
            "No records matching your query were found",
        )):
            if signals:
                signals.log.emit("WARN", "  检索无结果")
            return all_items

        # 只有1条结果 → PATENTSCOPE 直接跳到详情页
        if "detail.jsf" in self.page.url:
            import re as _re
            m = _re.search(r'docId=([^&]+)', self.page.url)
            doc_id = m.group(1) if m else ""
            # 从详情页提取摘要信息
            try:
                detail = await self._extract_detail_page(doc_id)
                if detail and signals:
                    pub_label = detail.get("publication_number", doc_id)
                    signals.log.emit("INFO", f"  仅1条结果，直接进入详情页: {pub_label}")
                pub_num = (detail or {}).get("publication_number") or doc_id
                item = {
                    "doc_id": doc_id,
                    "publication_number": pub_num,
                    "title": detail.get("title", ""),
                    "abstract_snippet": (detail.get("abstract") or "")[:300],
                    "ipc": detail.get("ipc", ""),
                    "applicant": detail.get("applicant", ""),
                    "source_query": query,
                }
                all_items.append(item)
            except Exception as e:
                if signals:
                    signals.log.emit("WARN", f"  详情页提取失败: {e}")
            if signals:
                signals.log.emit("SUCCESS", f"  摘要检索完成: {len(all_items)} 篇")
            return all_items

        # 先解析首页（不切200），拿到总结果数判断是否需要切换
        await self._wait_for_results()
        page_items = await self._parse_results_table()
        if not page_items:
            if signals:
                signals.log.emit("WARN", "  首页未解析到结果")
            return all_items

        total_count = await self._get_total_result_count()
        if signals:
            signals.log.emit("INFO",
                f"  首页解析: {len(page_items)} 条, 检索总结果: {total_count or '?'} 条")

        # 只在需要更多结果时才切换每页200条（省掉不必要的等待）
        if total_count and total_count > len(page_items) and len(page_items) < max_results:
            if signals:
                signals.log.emit("INFO",
                    f"  仅{len(page_items)}条/共{total_count}条，切换200...")
            await self._set_max_page_size(signals)
            page_items = await self._parse_results_table()
            if signals:
                signals.log.emit("INFO",
                    f"  切200后: {len(page_items)} 条")
        elif not total_count:
            await self._set_max_page_size(signals)
            page_items = await self._parse_results_table()

        remaining = min(len(page_items), max_results)
        all_items.extend(page_items[:remaining])

        # 总数 ≤ 每页条数(200) → 全部在一页内，无需翻页
        if total_count and total_count <= 200:
            if signals:
                signals.log.emit("INFO",
                    f"  总结果 {total_count} ≤ 200，无需翻页")
                signals.log.emit("SUCCESS",
                    f"  摘要检索完成: {len(all_items)} 篇")
            return all_items

        # 总数 > 200 或无法获取总数 → 需要翻页
        page_num = 1
        prev_ids = {a.get("doc_id", "") for a in page_items}
        while len(all_items) < max_results:
            if signals:
                signals.progress.emit(
                    int(10 + len(all_items) / max_results * 20) if max_results else 30,
                    f"解析结果页 {len(all_items)}/{max_results}")

            if signals:
                signals.log.emit("INFO", "  翻到下一页...")
            has_next = await self._go_to_next_page()
            if not has_next:
                break
            page_num += 1

            await self._wait_for_results()
            page_items = await self._parse_results_table()

            if not page_items:
                if signals:
                    signals.log.emit("WARN", f"  第{page_num}页未解析到结果")
                break

            # 检测翻页重复：本页全部 doc_id 都在上一页出现过
            this_ids = {a.get("doc_id", "") for a in page_items}
            if this_ids and prev_ids and this_ids.issubset(prev_ids):
                if signals:
                    signals.log.emit("INFO",
                        f"  第{page_num}页与上页重复，已是最后一页（共{len(all_items)}条）")
                break
            prev_ids = this_ids

            # 过滤已获取的条目，避免 pageSize 变化导致重复
            existing_ids = {a.get("doc_id", "") for a in all_items}
            new_items = [item for item in page_items
                         if item.get("doc_id", "") not in existing_ids]
            if not new_items:
                if signals:
                    signals.log.emit("INFO",
                        f"  第{page_num}页全部重复，已是最后一页（共{len(all_items)}条）")
                break

            remaining = max_results - len(all_items)
            took = min(len(new_items), remaining)
            all_items.extend(new_items[:remaining])

            if signals:
                if took < len(new_items):
                    signals.log.emit("INFO",
                        f"  结果页解析: {len(new_items)} 条(取{took}条), 累计 {len(all_items)}/{max_results}")
                else:
                    signals.log.emit("INFO",
                        f"  结果页解析: {len(new_items)} 条, 累计 {len(all_items)}/{max_results}")

            if len(all_items) >= max_results:
                break

        if signals:
            signals.log.emit("SUCCESS", f"  摘要检索完成: {len(all_items)} 篇")
        return all_items

    # ================================================================
    # 阶段2: 按需抓取详情
    # ================================================================

    async def fetch_detail(self, doc_id: str) -> Optional[dict]:
        """获取专利详情（直连优先，失败/403/空内容自动切搜索方式）。

        当 prefer_cn_family 开启时：若当前专利为非CN专利，
        自动查找专利族中的 CN 专利并抓取之。
        """
        # 先试直连（快）
        detail_url = f"https://patentscope2.wipo.int/search/zh/detail.jsf?docId={doc_id}"
        try:
            await self.page.goto(detail_url, timeout=60000, wait_until="commit")
            # ⚠️ 第一时间检测 403，不让 h1 白白等 10s
            if await self._check_blocked(self.page):
                raise RuntimeError("403 Forbidden")
            try:
                await self.page.wait_for_selector("h1", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)

            # ── 优先使用中国同族专利 ──
            if self.settings.search_prefer_cn_family:
                cn_family = await self._find_cn_in_patent_family(doc_id)
                if cn_family:
                    # 导航到 CN 同族专利页面
                    cn_url = (
                        f"https://patentscope2.wipo.int/search/zh"
                        f"/detail.jsf?docId={cn_family}"
                    )
                    await self.page.goto(cn_url, timeout=60000,
                                         wait_until="commit")
                    try:
                        await self.page.wait_for_selector("h1", timeout=10000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    result = await self._extract_detail_page(cn_family)
                    if result:
                        result["_cn_family_original"] = doc_id
                        result["_cn_family_replaced_by"] = cn_family
                        return result

            result = await self._extract_detail_page(doc_id)
            if result:
                return result
            raise Exception("空内容")
        except Exception:
            pass
        # 降级：搜索方式
        return await self.fetch_detail_via_search(doc_id)

    async def fetch_detail_via_search(self, search_term: str,
                                       signals=None) -> Optional[dict]:
        """通过搜索公布号获取专利详情。

        自动去掉末尾种类码（A/B/U等），提高 PATENTSCOPE 搜索命中率。
        """
        import re as _re
        _EXTRACT_FAIL_REASON.set("")
        # 去空格、去末尾种类码
        cleaned = _re.sub(r'\s+', '', search_term.strip())
        cleaned = _re.sub(r'[ABU]\d?$', '', cleaned)

        async def _try_search(term: str) -> Optional[dict]:
            nonlocal signals
            try:
                # 导航到搜索页（用 JS 跳转代替 page.goto，更像真实用户行为）
                await self.page.evaluate(
                    f'window.location.href = "{self.settings.patentscope_search_url}"')
                # 等导航稳定：JS 跳转后立即 evaluate 会因页面切换报
                # "Execution context was destroyed"，先等加载完成
                try:
                    await self.page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass
                await asyncio.sleep(1)
                # ⚠️ 先快速检测是否403，不等输入框超时
                for _ in range(5):  # 最多等 ~10s
                    await asyncio.sleep(2)
                    if await PatentscopeScraper._check_blocked(self.page):
                        raise RuntimeError(
                            "PATENTSCOPE 返回 403 Forbidden — 当前IP已被限流")
                    # 输入框出现了就继续
                    inp_check = self.page.locator(
                        "#simpleSearchForm\\:fpSearch\\:input")
                    if await inp_check.count() > 0:
                        break
                # 等搜索框出现
                try:
                    await self.page.wait_for_selector(
                        "#simpleSearchForm\\:fpSearch\\:input",
                        timeout=5000)
                except Exception:
                    await asyncio.sleep(2)

                # 填入公布号并提交搜索
                inp = self.page.locator(
                    "#simpleSearchForm\\:fpSearch\\:input")
                if await inp.count() > 0:
                    await inp.wait_for(state="visible", timeout=5000)
                    await inp.fill(term)
                    await asyncio.sleep(random.uniform(0.3, 0.6))
                    btn = self.page.locator("button[id*='fpSearch']").first
                    if await btn.count() > 0:
                        try:
                            await btn.click(no_wait_after=True, timeout=15000)
                        except Exception:
                            try:
                                await self.page.evaluate(
                                    '() => { var b = document.querySelector('
                                    '"button[id*=\'fpSearch\']"); if(b) b.click(); }')
                            except Exception:
                                pass
                        # 快速检查无结果，避免白等 20s
                        for _ in range(3):
                            await asyncio.sleep(1.0)
                            try:
                                bt = await self.page.evaluate(
                                    "() => document.body?.innerText"
                                    "?.substring(0, 2000) || ''")
                            except Exception:
                                continue  # 页面正在跳转，执行上下文销毁，跳过本轮
                            if any(m in bt for m in (
                                "没有找到符合您检索的结果",
                                "未找到结果", "No results found",
                            )):
                                _EXTRACT_FAIL_REASON.set("搜索未命中(无结果)")
                                return None
                        try:
                            await self.page.wait_for_url(
                                "**/detail.jsf*", timeout=20000)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)

                cur = self.page.url
                if "detail.jsf" in cur:
                    m = _re.search(r'docId=([^&]+)', cur)
                    doc_id = m.group(1) if m else term
                    if signals:
                        signals.log.emit("INFO",
                            f"    搜索 {term} → 详情页")
                    return await self._extract_detail_page(doc_id)

                # 多条结果 → 点击第一条
                link = self.page.locator("a[href*='detail.jsf']").first
                if await link.count() > 0:
                    try:
                        await link.click(timeout=15000)
                    except Exception:
                        try:
                            await self.page.evaluate(
                                '() => { var a = document.querySelector('
                                '"a[href*=\'detail.jsf\']"); if(a) a.click(); }')
                        except Exception:
                            pass
                    await self.page.wait_for_url(
                        "**/detail.jsf*", timeout=15000)
                    await asyncio.sleep(1)
                    m = _re.search(r'docId=([^&]+)', self.page.url)
                    doc_id = m.group(1) if m else term
                    if signals:
                        signals.log.emit("INFO",
                            f"    搜索 {term} → 点击第一条结果")
                    return await self._extract_detail_page(doc_id)

                _EXTRACT_FAIL_REASON.set("搜索结果页未找到详情链接")
                return None
            except Exception as e:
                if signals:
                    signals.log.emit("WARN", f"    搜索 {term} 失败: {e}")
                _EXTRACT_FAIL_REASON.set(f"搜索异常: {e}")
                return None

        # 先用去掉种类码的号搜，不行再用原始号
        result = await _try_search(cleaned)
        if not result and cleaned != search_term.replace(" ", ""):
            if signals:
                signals.log.emit("INFO",
                    f"    {cleaned} 未命中，尝试原始号 {search_term}")
            result = await _try_search(search_term.replace(" ", ""))
        return result

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
                # 摘要回退：详情页提取不到摘要时，用搜索结果的 abstract_snippet
                if not merged.get("abstract") and p.get("abstract_snippet"):
                    merged["abstract"] = p["abstract_snippet"]
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
        doc_ids = []
        for p in patents:
            did = p.get("doc_id") or p.get("publication_number", "")
            if did and did not in meta_map:
                doc_ids.append(did)
            meta_map[did] = p
            pub = p.get("publication_number", "")
            if pub and pub != did:
                meta_map[pub] = p

        # 断点续传：跳过已存在且内容有效的文件
        remaining = []
        skipped = 0
        for did in doc_ids:
            safe = _safe_filename(did)
            fpath = out / f"{safe}.json"
            if fpath.exists():
                try:
                    existing = json_module.loads(fpath.read_text(encoding="utf-8"))
                    if is_cached_patent_valid(existing):
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
        consecutive_403 = 0
        batch_count = 0
        failed_ids = []           # 本轮失败的 doc_id，供重试用
        # 记录每个 doc_id 在第一轮的原始位置（1-based），重试时保持原编号
        _orig_idx = {did: i + 1 for i, did in enumerate(remaining)}
        BATCH_SIZE = 15
        current_context = self.page.context
        current_page = self.page

        async def _restart_browser():
            """杀浏览器进程重启，彻底换 session"""
            nonlocal current_context, current_page
            from src.web_automation.browser_manager import BrowserManager
            await BrowserManager.shutdown()
            await asyncio.sleep(2)
            mgr = BrowserManager(self.settings)
            ctx, pg = await mgr.launch()
            current_context = ctx
            current_page = pg
            self.page = pg          # 同步更新 scraper 的 page 引用
            self.human = HumanBehavior(self.settings)  # 新 browser 需要新 human

        async def _fetch_one(doc_id: str):
            nonlocal success_count, fail_count, consecutive_403, batch_count
            async with sem:
                # 每 N 篇换一次浏览器，避免限流
                if batch_count >= BATCH_SIZE:
                    from src.web_automation.browser_manager import BrowserManager
                    new_browser = BrowserManager.switch_channel()
                    if signals:
                        signals.log.emit("INFO",
                            f"    已下载 {success_count} 篇，切换至 {new_browser}...")
                    await _restart_browser()
                    batch_count = 0
                    await asyncio.sleep(2)

                # 遇到403：切浏览器 + 换代理节点(换IP) + 冷却
                if consecutive_403 >= 1:
                    from src.web_automation.browser_manager import BrowserManager
                    new_browser = BrowserManager.switch_channel(on_403=True)
                    # 换 Clash 代理节点 = 换出口 IP，能摆脱按 IP 的限流。
                    # switch_channel 只换浏览器（换 session/指纹），不换 IP。
                    ip_switched = False
                    try:
                        ip_switched = await BrowserManager.rotate_proxy(self.settings)
                    except Exception:
                        pass
                    await _restart_browser()
                    cool = random.uniform(5, 15)
                    if signals:
                        signals.log.emit("WARN",
                            f"    遇到 403，切换至 {new_browser}"
                            f"{' + 换IP节点' if ip_switched else ''}"
                            f" + 冷却 {cool:.0f}s...")
                    await asyncio.sleep(cool)
                    consecutive_403 = 0
                    batch_count = 0

                # ── 引擎分发：wipo 全链路浏览器 / google 全链路 Google ──
                #   wipo   → 全部走 PATENTSCOPE 浏览器（原行为，含 CN 同族）
                #   google → 全部走 Google（免浏览器）；无全文即失败，不降级
                mode = ("google" if self.settings.search_source == "google"
                        else "wipo")

                # 随机延迟（逐批递增）
                if mode == "google":
                    # Google 免登录，无需长延迟防 403；保留 ~1s 最小间隔防反爬
                    delay = 0.5 + random.uniform(0, 1.0)
                else:
                    delay = 5.0 + random.uniform(0, 3.0)
                    if batch_count > 5:
                        delay = 8.0 + random.uniform(0, 5.0)
                await asyncio.sleep(delay)
                meta = meta_map.get(doc_id, {})
                pub = meta.get("publication_number") or doc_id or ""
                # 纯数字号缺国别前缀（PATENTSCOPE 搜索页常见，如 US 公布号
                # 显示为 "20210343831"）→ 用 doc_id 的国别前缀补全为 Google
                # 可解析的公开号（如 "US20210343831"）。
                if re.match(r'^\d', pub) and re.match(r'^[A-Z]{2}', doc_id or ""):
                    pub = doc_id[:2] + pub
                if mode == "google":
                    gp_detail = None
                    try:
                        gp_detail = await asyncio.to_thread(
                            fetch_patent_text, pub,
                            self.settings.web_proxy,
                            self.settings.google_patents_timeout,
                            meta)
                    except Exception:
                        gp_detail = None
                    if gp_detail:
                        file_key = pub
                        safe = _safe_filename(file_key)
                        fpath = out / f"{safe}.json"
                        fpath.write_text(
                            json_module.dumps(gp_detail, indent=2,
                                              ensure_ascii=False, default=str),
                            encoding="utf-8")
                        success_count += 1
                        if signals:
                            idx = _orig_idx.get(
                                doc_id, success_count + fail_count)
                            signals.log.emit("INFO",
                                f"    ✓G [{idx}/{len(remaining)}] {pub}")
                        return
                    # google 模式：无全文直接记为失败，不降级浏览器
                    fail_count += 1
                    failed_ids.append(doc_id)
                    safe = _safe_filename(doc_id)
                    fpath = out / f"{safe}.json"
                    fpath.write_text(
                        json_module.dumps({
                            "doc_id": doc_id,
                            "fetch_status": "failed",
                            "url": (f"https://patents.google.com/patent/"
                                    f"{pub}/zh"),
                            "error": "Google Patents 无全文",
                        }, indent=2, ensure_ascii=False),
                        encoding="utf-8")
                    if signals:
                        idx = _orig_idx.get(
                            doc_id, success_count + fail_count)
                        signals.log.emit("WARN",
                            f"    ✗G [{idx}/{len(remaining)}] "
                            f"{pub}: Google 无全文")
                    return

                pg = await current_context.new_page()
                is_403 = False
                detail_url = ""
                try:
                    stored_url = meta.get("detail_url")
                    detail_url = stored_url or (
                        f"https://patentscope2.wipo.int/search/zh/detail.jsf"
                        f"?docId={doc_id}"
                    )
                    # 直接进详情链接（已验证：无需先建 session，detail.jsf 能正常渲染）
                    await pg.goto(detail_url, timeout=60000,
                                  wait_until="commit")
                    # ⚠️ 第一时间检测 403
                    if await self._check_blocked(pg):
                        is_403 = True
                        raise RuntimeError("403 Forbidden")
                    try:
                        await pg.wait_for_selector("h1", timeout=8000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                    # ── 优先使用中国同族专利 ──
                    actual_doc_id = doc_id
                    cn_family_note = None
                    if self.settings.search_prefer_cn_family:
                        cn_family = await self._find_cn_in_patent_family(
                            doc_id, page=pg)
                        if cn_family:
                            # 导航到 CN 同族专利页面
                            cn_url = (
                                f"https://patentscope2.wipo.int/search/zh"
                                f"/detail.jsf?docId={cn_family}"
                            )
                            await pg.goto(cn_url, timeout=60000,
                                          wait_until="commit")
                            try:
                                await pg.wait_for_selector("h1", timeout=8000)
                            except Exception:
                                pass
                            await asyncio.sleep(1)
                            actual_doc_id = cn_family
                            cn_family_note = cn_family
                            if signals:
                                signals.log.emit("INFO",
                                    f"    🔄 专利族替换: {doc_id} → CN {cn_family}")

                    detail = await self._extract_detail_page(
                        actual_doc_id, page=pg)
                    direct_reason = ""
                    if not detail:
                        direct_reason = (_EXTRACT_FAIL_REASON.get()
                                         or "未知原因")

                    # 记录专利族替换信息
                    if detail and cn_family_note:
                        detail["_cn_family_original"] = doc_id
                        detail["_cn_family_replaced_by"] = cn_family_note

                    # 直连提取失败 → 搜索兜底（即使有 stored_url 也搜一次：
                    # 无 session 直连 detail.jsf 经常渲染失败，搜索能重建 session 并命中）
                    search_reason = ""
                    if not detail:
                        self.page = pg
                        detail = await self.fetch_detail_via_search(
                            actual_doc_id, signals=signals)
                        if not detail:
                            search_reason = (_EXTRACT_FAIL_REASON.get()
                                             or "未知原因")

                    if not detail:
                        if search_reason:
                            reason = (f"直连失败: {direct_reason}; "
                                      f"搜索失败: {search_reason}")
                        else:
                            reason = direct_reason
                        raise RuntimeError(f"内容无效: {reason}")
                    consecutive_403 = 0
                    batch_count += 1
                    detail["fetch_status"] = "ok"

                    meta = meta_map.get(doc_id, {})
                    # 摘要回退：详情页提取不到摘要时，用搜索结果的 abstract_snippet
                    if not detail.get("abstract") and meta.get("abstract_snippet"):
                        detail["abstract"] = meta["abstract_snippet"]
                    pub_from_search = meta.get("publication_number", "")
                    # CN同族替换后：文件用原始doc_id命名（避免与CN专利自身缓存冲突），
                    # 但标题/权利要求等保留CN专利的内容
                    if cn_family_note:
                        # 保留 CN 专利的 publication_number 作为主标识
                        # 文件用原始 doc_id 命名，防止与已在列表中的 CN 专利文件冲突
                        file_key = doc_id
                    elif pub_from_search:
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
                        idx = _orig_idx.get(doc_id, success_count + fail_count)
                        signals.log.emit("INFO",
                            f"    ✓ [{idx}/{len(remaining)}] "
                            f"{detail.get('publication_number', doc_id)}")
                except Exception as e:
                    fail_count += 1
                    if is_403:
                        consecutive_403 += 1  # 真403才触发切浏览器
                    failed_ids.append(doc_id)
                    safe = _safe_filename(doc_id)
                    fpath = out / f"{safe}.json"
                    fpath.write_text(
                        json_module.dumps({
                            "doc_id": doc_id,
                            "fetch_status": "failed",
                            "url": detail_url or "",
                            "error": str(e)[:200],
                        }, indent=2, ensure_ascii=False),
                        encoding="utf-8")
                    if signals:
                        meta = meta_map.get(doc_id, {})
                        label = meta.get("publication_number", doc_id)
                        reason = str(e)[:120]
                        idx = _orig_idx.get(doc_id, success_count + fail_count)
                        url = detail_url or f"docId={doc_id}"
                        signals.log.emit("WARN",
                            f"    ✗ [{idx}/{len(remaining)}] "
                            f"{label}: {reason} | {url}")
                finally:
                    await pg.close()
                    # 请求间微小延迟，避免触发限流
                    await asyncio.sleep(random.uniform(0.3, 0.8))

        await asyncio.gather(
            *[_fetch_one(did) for did in remaining],
            return_exceptions=True)

        # ── 二轮补下载 ──
        if failed_ids:
            retry_list = list(failed_ids)
            failed_ids.clear()
            if signals:
                signals.log.emit("INFO",
                    f"  === 二轮补下载 {len(retry_list)} 篇 === ")
            from src.web_automation.browser_manager import BrowserManager
            BrowserManager.switch_channel()
            await _restart_browser()
            consecutive_403 = 0
            batch_count = 0
            await asyncio.sleep(3)
            await asyncio.gather(
                *[_fetch_one(did) for did in retry_list],
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

    # 403 / 被封标记
    _BLOCKED_MARKERS = (
        "403", "FORBIDDEN", "Access Denied", "Request Rejected",
        "访问被拒绝", "ERROR 403",
    )

    @staticmethod
    async def _check_blocked(page, timeout: float = 3.0) -> bool:
        """快速检测页面是否为 403/被封页面。

        取 body 前 500 字符检查，比等某个 DOM 元素超时快得多。
        返回 True 表示已被封。
        """
        try:
            body = await page.evaluate(
                "() => (document.body && document.body.innerText || '').substring(0, 500)")
            return any(m in body for m in PatentscopeScraper._BLOCKED_MARKERS)
        except Exception:
            return False

    # ================================================================
    # 搜索表单
    # ================================================================

    async def _navigate_and_search(self, query: str, signals=None):
        if signals:
            signals.log.emit("INFO", "  正在访问 PATENTSCOPE...")
        search_url = self.settings.patentscope_search_url
        await self.page.goto(search_url, timeout=60000, wait_until="load")
        # JSF 页面可能需要额外时间初始化组件
        await asyncio.sleep(3)

        if signals:
            signals.log.emit("INFO", "  正在提交检索式...")
        await self._fill_and_submit(query)

        if signals:
            signals.log.emit("INFO", "  等待搜索结果...")
        # 先等 URL 跳到结果页（快），再等结果表格出现
        try:
            await self.page.wait_for_url("**/result.jsf*", timeout=60000)
        except Exception:
            pass
        try:
            await self.page.wait_for_selector(
                ".ps-patent-result, .trans-result-list-row, table.patent-result-list",
                timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1)

    async def _set_max_page_size(self, signals=None):
        """切到每页最多条数（200）—— 组合方案 + 变化检测。

        关键：PATENTSCOPE 页面同时存在两个"每页条数"下拉框——
          settingsForm:lengthOption:input  （隐藏，设置表单里的默认值偏好）
          resultListCommandsForm:perPage:input（可见，真正控制当前结果列表）
        因此对每个含200选项的 select 评分（perPage 权重最高、可见加分、
        lengthOption 降权），取最高分者，绝不误信 settingsForm 那个。
        然后按顺序尝试：
          方式A: Playwright select_option(force=True) —— 原生/可信事件，
                能同时触发 JSF inline onchange 和 addEventListener 绑定。
          方式B: 纯 JS 改值 + 直接调用 sel.onchange() + 派发 change。

        每次切换后都用 _wait_for_results_stable(min_change_from=old_count)
        等待"行数真正变化"再判定稳定——旧10行连续两次相同不再被误判为稳定。
        若两次切换后行数仍不变，把页面所有下拉框 DOM 导出到
        tools/debug_select_dom.json 供进一步诊断。
        """
        old_count = await self._count_result_rows()

        # ── 1. 定位目标下拉框（评分选最优）────────────────────────────
        # 页面上存在两个"每页条数"下拉框：
        #   settingsForm:lengthOption:input  —— 隐藏，是"设置"里的默认值偏好
        #   resultListCommandsForm:perPage:input —— 可见，真正控制当前结果列表
        # 绝不能命中 settingsForm 那个（它可能已是200但列表仍是10）。
        # 因此对每个含200选项的 select 评分，perPage 权重最高、可见加分、
        # lengthOption 降权，取最高分者并直接打标记（一个 evaluate 完成）。
        target = await self.page.evaluate('''() => {
            function has200(s) {
                for (var j = 0; j < s.options.length; j++) {
                    if (s.options[j].value === "200") return true;
                }
                return false;
            }
            function score(s) {
                var id = (s.id || "").toLowerCase();
                var name = (s.name || "").toLowerCase();
                var sc = 0;
                if (id.indexOf("perpage") !== -1 || name.indexOf("perpage") !== -1) sc += 100;
                if (id.indexOf("pagesize") !== -1) sc += 90;
                if (id.indexOf("rowsperpage") !== -1) sc += 90;
                if (id.indexOf("itemsperpage") !== -1) sc += 90;
                if (id.indexOf("resultlist") !== -1) sc += 50;   // 结果列表表单
                if (id.indexOf("lengthoption") !== -1) sc += 10; // 设置表单，降权
                if (id.indexOf("length") !== -1) sc += 10;
                if (s.offsetParent !== null) sc += 20;           // 可见优先
                return sc;
            }

            var allSelects = document.querySelectorAll("select");
            var cands = [];
            for (var i = 0; i < allSelects.length; i++) {
                var s = allSelects[i];
                if (!has200(s)) continue;
                cands.push({sel: s, sc: score(s)});
            }
            if (!cands.length) return {status: "not_found"};

            cands.sort(function(a, b) { return b.sc - a.sc; });
            var best = cands[0].sel;
            best.setAttribute("data-ps-length-target", "1");

            var candInfo = cands.map(function(c) {
                return {id: c.sel.id || "", name: c.sel.name || "",
                        value: c.sel.value, score: c.sc,
                        visible: !!(c.sel.offsetParent !== null)};
            });
            var opts = [];
            for (var j = 0; j < best.options.length; j++) {
                opts.push({v: best.options[j].value, t: best.options[j].text});
            }
            return {
                status: "found",
                id: best.id || "", name: best.name || "",
                className: (best.className || "").toString(),
                currentValue: best.value,
                visible: !!(best.offsetParent !== null),
                hasOnchange: typeof best.onchange === "function",
                options: opts,
                reason: "score " + cands[0].sc,
                candidates: candInfo,
            };
        }''')
        target_loc = self.page.locator("select[data-ps-length-target]")

        if target.get("status") != "found":
            if signals:
                signals.log.emit("WARN",
                    "  未找到分页下拉框（JS 定位失败）")
            return

        if signals:
            cands = target.get("candidates") or []
            cand_str = " | ".join(
                f"{c.get('id') or '(无id)'}={c.get('value')}"
                f"(分{c.get('score')},"
                f"{'可见' if c.get('visible') else '隐藏'})"
                for c in cands)
            signals.log.emit("DEBUG",
                f"  候选下拉框: {cand_str}")

        if target.get("currentValue") == "200":
            if signals:
                signals.log.emit("INFO",
                    "  每页已是 200 条，等待数据稳定...")
            await self._wait_for_results_stable(signals, label="已是200")
            return

        if signals:
            signals.log.emit("INFO",
                f"  切换每页条数: {target.get('currentValue')} → 200 "
                f"(id={target.get('id') or '(无id)'}, "
                f"{target.get('reason')}, "
                f"visible={target.get('visible')}, "
                f"onchange={target.get('hasOnchange')})")

        async def _wait_change():
            return await self._wait_for_results_stable(
                signals, label="切换200", max_wait=15.0,
                min_change_from=old_count)

        # ── 方式A: Playwright select_option（原生事件）──
        switched = False
        try:
            await target_loc.select_option(
                value="200", force=True, no_wait_after=True, timeout=8000)
            switched = True
        except Exception as e:
            if signals:
                signals.log.emit("WARN",
                    f"  select_option 失败({str(e)[:80]})，尝试 JS 方式...")

        if not switched:
            # ── 方式B: 纯 JS 改值 + onchange + 派发 change ──
            try:
                await self.page.evaluate('''() => {
                    var sel = document.querySelector(
                        "select[data-ps-length-target]");
                    if (!sel) return;
                    sel.value = "200";
                    if (typeof sel.onchange === "function") sel.onchange();
                    sel.dispatchEvent(new Event("change",
                        {bubbles: true, cancelable: true}));
                }''')
            except Exception:
                pass

        # ── 3. 等待行数真正变化并稳定 ─────────────────────────────
        stable_count = await _wait_change()

        if stable_count > old_count:
            if signals:
                signals.log.emit("INFO",
                    f"  页面刷新完成: {old_count} → {stable_count} 行")
            return

        # 变化失败：可能页面整体刷新导致选择被重置，用 JS 再应用一次
        if signals:
            signals.log.emit("WARN",
                f"  切换后行数未增加（{old_count} → {stable_count}），"
                f"再用 JS 应用一次...")
        try:
            await self.page.evaluate('''() => {
                // 与定位阶段相同的评分逻辑，选出真正控制结果列表的下拉框
                function has200(s) {
                    for (var j = 0; j < s.options.length; j++) {
                        if (s.options[j].value === "200") return true;
                    }
                    return false;
                }
                function score(s) {
                    var id = (s.id || "").toLowerCase();
                    var name = (s.name || "").toLowerCase();
                    var sc = 0;
                    if (id.indexOf("perpage") !== -1 || name.indexOf("perpage") !== -1) sc += 100;
                    if (id.indexOf("pagesize") !== -1) sc += 90;
                    if (id.indexOf("rowsperpage") !== -1) sc += 90;
                    if (id.indexOf("itemsperpage") !== -1) sc += 90;
                    if (id.indexOf("resultlist") !== -1) sc += 50;
                    if (id.indexOf("lengthoption") !== -1) sc += 10;
                    if (id.indexOf("length") !== -1) sc += 10;
                    if (s.offsetParent !== null) sc += 20;
                    return sc;
                }
                var allSelects = document.querySelectorAll("select");
                var best = null, bestSc = -1;
                for (var i = 0; i < allSelects.length; i++) {
                    var s = allSelects[i];
                    if (!has200(s)) continue;
                    var sc = score(s);
                    if (sc > bestSc) { best = s; bestSc = sc; }
                }
                if (!best) return;
                best.value = "200";
                if (typeof best.onchange === "function") best.onchange();
                best.dispatchEvent(new Event("change",
                    {bubbles: true, cancelable: true}));
            }''')
        except Exception:
            pass
        stable_count = await _wait_change()
        if signals:
            signals.log.emit("INFO",
                f"  二次应用后: {stable_count} 行")

        if stable_count <= old_count:
            # ── 4. 仍失败：导出 DOM 诊断信息 ──
            try:
                diag = await self.page.evaluate('''() => {
                    var out = [];
                    var all = document.querySelectorAll("select");
                    for (var i = 0; i < all.length; i++) {
                        var s = all[i];
                        out.push({
                            id: s.id || "", name: s.name || "",
                            className: (s.className || "").toString(),
                            value: s.value,
                            visible: !!(s.offsetParent !== null),
                            hasOnchange: typeof s.onchange === "function",
                            options: Array.from(s.options).map(
                                function(o) {
                                    return {v: o.value, t: o.text};
                                }),
                        });
                    }
                    return out;
                }''')
                import json as _json
                from pathlib import Path
                dump_path = (Path(__file__).resolve().parent.parent.parent
                             / "tools" / "debug_select_dom.json")
                dump_path.write_text(
                    _json.dumps(diag, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                if signals:
                    signals.log.emit("WARN",
                        f"  已导出下拉框 DOM 到 {dump_path}，请查看")
            except Exception:
                pass

    # ── DOM 稳定性轮询 ─────────────────────────────────────────────

    async def _count_result_rows(self) -> int:
        """返回当前页面的结果行数。"""
        try:
            return await self.page.evaluate('''() => {
                return document.querySelectorAll(
                    "tr.trans-result-list-row, .ps-patent-result").length;
            }''')
        except Exception:
            return 0

    async def _wait_for_results_stable(self, signals=None,
                                        label: str = "",
                                        max_wait: float = 12.0,
                                        poll_interval: float = 0.6,
                                        min_change_from: int | None = None) -> int:
        """轮询 DOM 直到结果行数稳定。

        每 poll_interval 秒检查一次行数，连续 2 次相同即认为稳定。
        最长等待 max_wait 秒，超时返回当前行数。

        min_change_from: 若传入（切换每页条数前的旧行数），会先等待行数
            真正发生变化（!= 旧值）再进入稳定性判定，避免"切换事件尚未生效
            （AJAX 刷新延迟）时旧行数连续两次相同被误判为稳定"的问题。

        同时缓存总结果数到 _total_from_page_text。
        """
        prev_count = -1
        stable_hits = 0
        max_polls = int(max_wait / poll_interval)
        # 切换场景下：先等行数变化，变化前不做稳定性判定
        changed = min_change_from is None

        for attempt in range(max_polls):
            await asyncio.sleep(poll_interval)

            count = await self._count_result_rows()
            if count == 0:
                # 刷新期间表格可能暂时清空（JSF AJAX 重建 DOM）
                prev_count = -1
                stable_hits = 0
                continue

            # 切换场景：等待行数真正变化
            if not changed:
                if count != min_change_from:
                    changed = True
                    if signals:
                        signals.log.emit("DEBUG",
                            f"  _wait_stable{label}: 行数已变化 "
                            f"{min_change_from} → {count}")
                else:
                    if signals and attempt == 0:
                        signals.log.emit("DEBUG",
                            f"  _wait_stable{label}: 等待行数变化"
                            f"（当前仍为 {count} 行）...")
                    prev_count = count
                    continue

            # 同时尝试提取总结果数（best-effort）
            if self._total_from_page_text <= 0:
                total = await self._get_total_result_count()
                if total:
                    self._total_from_page_text = total

            if count == prev_count and count > 0:
                stable_hits += 1
                if stable_hits >= 2:
                    if signals:
                        signals.log.emit("DEBUG",
                            f"  _wait_stable{label}: 稳定在 {count} 行 "
                            f"(总={self._total_from_page_text or '?'}, "
                            f"第{attempt+1}次轮询)")
                    return count
            else:
                stable_hits = 0
                prev_count = count

            if signals and attempt == 0:
                signals.log.emit("DEBUG",
                    f"  _wait_stable{label}: 轮询中... 当前 {count} 行")

        # 超时：返回最后的行数
        if signals:
            signals.log.emit("DEBUG",
                f"  _wait_stable{label}: 超时 ({max_wait}s)，"
                f" 最后行数={prev_count}"
                + ("（行数从未变化，切换可能未生效）" if not changed else ""))
        return max(prev_count, 0)

    async def _fill_and_submit(self, query: str):
        # 步骤1: 使用 Playwright locator 填入检索式（自动等待元素、防导航中断）
        try:
            input_locator = self.page.locator(
                "#simpleSearchForm\\:fpSearch\\:input").first
            # 等输入框前先快速检查是否已被封，避免白等 30s
            for _ in range(6):  # 最多等 ~18s，每 3s 检查一次是否被封
                if await input_locator.count() > 0:
                    break
                if await self._check_blocked(self.page):
                    raise RuntimeError(
                        "PATENTSCOPE 返回 403 Forbidden — 当前IP已被限流")
                await asyncio.sleep(3)
            await input_locator.wait_for(state="visible", timeout=5000)
            await input_locator.fill(query)
            await asyncio.sleep(0.5)
        except Exception as e:
            raise RuntimeError(f"搜索框填入失败: {e}")

        # 步骤2: 点击搜索按钮（JSF AJAX 不走标准导航，禁用导航等待）
        # Playwright click 可能因页面繁忙而超时 → JS click 兜底
        try:
            btn_locator = self.page.locator("button[id*='fpSearch']").first
            await btn_locator.click(no_wait_after=True, timeout=15000)
        except Exception:
            try:
                await self.page.evaluate(
                    '() => { var b = document.querySelector('
                    '"button[id*=\'fpSearch\']"); if(b) b.click(); }')
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
                    var href = linkEl.href;
                    // 去掉 _cid 等 session 参数，只保留 docId
                    href = href.replace(/[&?]_cid=[^&]*/g, '');
                    item.detail_url = href;
                    var m = href.match(/docId=([^&]+)/);
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
                var appMatch = rowText.match(/申请号\\s*([\\d.X]+)/);
                if (appMatch) item.application_number = appMatch[1];

                if (item.publication_number || item.title) { results.push(item); }
            });
            return results;
        }''')
        return items

    async def _get_total_result_count(self) -> int | None:
        """从 PATENTSCOPE 结果页提取总结果数。

        优先使用 _verify_page_size 缓存的 _total_from_page_text，
        否则尝试多种 DOM 位置和文本模式，返回整数或 None。
        """
        # 优先使用已验证过的缓存值
        cached = getattr(self, "_total_from_page_text", 0) or 0
        if cached > 0:
            return cached

        try:
            count = await self.page.evaluate('''() => {
                var body = (document.body && document.body.innerText) || "";
                // 模式1: "523 个结果" / "523 results"
                var m = body.match(/(\\d[\\d,]*)\\s*(?:个结果|results?)/i);
                if (m) return parseInt(m[1].replace(/,/g, ""));
                // 模式2: "共 N 条" / "共找到 N 条" / "共 N 条结果"
                m = body.match(/共\\s*找到?\\s*(\\d[\\d,]*)\\s*条/);
                if (m) return parseInt(m[1].replace(/,/g, ""));
                // 模式3: "1-200 of 523" 取后面的 N
                m = body.match(/(\\d+)\\s*-\\s*(\\d+)\\s*(?:of|\\/|／|，共?)\\s*(\\d[\\d,]*)/i);
                if (m) return parseInt(m[3].replace(/,/g, ""));
                // 模式4: pagination 元素中的总数
                var paginators = document.querySelectorAll(
                    ".ui-paginator-current, .ps-paginator-total, " +
                    "[class*='result-count'], [class*='total-count'], " +
                    "[class*='pagination-total']");
                for (var i = 0; i < paginators.length; i++) {
                    var t = paginators[i].textContent;
                    var m2 = t.match(/(\\d[\\d,]*)/);
                    if (m2) {
                        var n = parseInt(m2[1].replace(/,/g, ""));
                        if (n > 0) return n;
                    }
                }
                return null;
            }''')
            return count if count and count > 0 else None
        except Exception:
            return None

    # ================================================================
    # 详情页提取
    # ================================================================

    async def _extract_detail_page(self, doc_id: str, page=None) -> dict | None:
        pg = page or self.page
        # 每次调用先清空上次失败原因，失败时再写入具体原因
        _EXTRACT_FAIL_REASON.set("")

        # 快速校验：不在详情页/明显错误 → 直接放弃
        cur_url = await pg.evaluate("() => window.location.href")
        body_check = await pg.evaluate(
            "() => document.body?.innerText?.substring(0, 500) || ''")
        _INVALID_MARKERS = [
            "未知专利申请", "ERROR 403", "403 FORBIDDEN",
            "内部错误", "页面未找到", "Page Not Found",
        ]
        if any(m in body_check for m in _INVALID_MARKERS):
            hit = next(m for m in _INVALID_MARKERS if m in body_check)
            _EXTRACT_FAIL_REASON.set(f"页面为错误页(命中'{hit}')")
            return None
        if "detail.jsf" not in cur_url:
            _EXTRACT_FAIL_REASON.set(f"未进入详情页(当前URL: {cur_url[:80]})")
            return None

        result = {
            "publication_number": "", "title": "", "abstract": "",
            "claims": "", "description": "", "ipc": "",
            "applicant": "", "inventor": "", "publication_date": "",
            "application_number": "",
        }

        # 书目数据
        biblio = await pg.evaluate('''() => {
            var data = {};
            // 优先用 textContent（含隐藏文本），innerText 可能漏掉折叠区域
            var body = (document.body && (document.body.textContent || document.body.innerText)) || "";
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
        ft = biblio.get("full_text", "")
        import re as _re
        ft = _re.sub(
            r'^(?:(?:反馈|检索|浏览|工具|设置|登录|PATENTSCOPE)\s*\n?)+',
            '', ft)
        ft = _re.sub(
            r'(?:永久链接\s*)?机器翻译WIPO\s*Translate[\s\S]*?PDF\s*版本为准\s*',
            '', ft)
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

        # 清理页面垃圾（WIPO翻译工具栏、OCR声明等）
        _page_junk = _re.compile(
            r'(?:永久链接\s*)?机器翻译WIPO\s*Translate[\s\S]*?PDF\s*版本为准\s*')
        if claims_text:
            claims_text = _page_junk.sub('', claims_text).strip()
        if desc_text:
            desc_text = _page_junk.sub('', desc_text).strip()

        # WO 等非 CN 专利没有独立 Claims/Description 标签，需从「全文」tab 提取
        if not claims_text or not desc_text:
            fulltext_raw = await self._click_and_extract_tab("FULLTEXT", page=pg)
            if fulltext_raw:
                # ⚠️ 必须用原始文本提取：clean_patent_html_text 会把换行压成
                # 单个空格，而 _extract_wo_claims 依赖换行锚定 claims 起点。
                # 提取成功后再单独 clean 成紧凑版给 AI 消费。
                if not claims_text:
                    claims_text = _extract_wo_claims(fulltext_raw)
                if not desc_text:
                    desc_text = _extract_wo_description(fulltext_raw)
                if claims_text:
                    claims_text = clean_patent_html_text(claims_text)
                if desc_text:
                    desc_text = clean_patent_html_text(desc_text)

        if claims_text:
            result["claims"] = claims_text[:10000]
        if desc_text:
            result["description"] = desc_text[:20000]

        # 校验：权利要求和说明书都非空才算有效，摘要可缺
        if not (result["claims"] and result["description"]):
            missing = [label for field, label in (
                ("claims", "权利要求"), ("description", "说明书"))
                if not result[field]]
            _EXTRACT_FAIL_REASON.set(
                f"{'/'.join(missing)}提取为空(tab未加载或无全文)")
            return None

        # 纯摘要 — 摘要/Abstract 双模式，中文优先
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
                # 降级4: 摘要/Abstract 到下一个字段标题之前
                m = re.search(
                    r'(?:摘要|Abstract)\s*\n+([\s\S]*?)'
                    r'(?=\n(?:申请号|公布号|IPC|申请人|发明人'
                    r'|权利要求|说明书|Claims?|Description'
                    r'|附图|图式|Drawings)\b)',
                    ft)
                if m:
                    abstract = m.group(1).strip()
            if not abstract:
                # 兜底: 任意 摘要/Abstract 开头直到结束
                m = re.search(r'(?:摘要|Abstract)\s*[\s\S]*?(?=Claims?|Description|权利要求|说明书|\Z)', ft)
                if m:
                    abstract = m.group(0)
                    abstract = re.sub(r'^(?:摘要|Abstract)\s*', '', abstract).strip()
            if abstract:
                result["abstract"] = abstract[:5000]

            # DOM 兜底：正则全失败时直接从页面元素提取
            if not result.get("abstract"):
                dom_abstract = await pg.evaluate('''() => {
                    // 按 class 名查找摘要元素
                    var selectors = [
                        "[class*='abstract']", "[class*='Abstract']",
                        ".ps-bibliographic-data--abstract",
                    ];
                    for (var i = 0; i < selectors.length; i++) {
                        try {
                            var els = document.querySelectorAll(selectors[i]);
                            for (var j = 0; j < els.length; j++) {
                                var t = els[j].textContent.trim();
                                if (t.length > 10 && t.length < 10000) return t;
                            }
                        } catch(e) {}
                    }
                    return "";
                }''')
                if dom_abstract and len(dom_abstract) > 10:
                    result["abstract"] = dom_abstract[:5000]

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
                await next_btn.click(timeout=15000)
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
            try:
                await tab.click(timeout=15000)
            except Exception:
                await pg.evaluate(
                    f'() => {{ var t = document.querySelector('
                    f'"a[href*=\'{href_keyword}\']"); if(t) t.click(); }}')
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

    # ── 辅助：专利族中查找 CN 专利 ─────────────────────────────────

    @staticmethod
    def _is_cn_patent(doc_id: str) -> bool:
        """判断 doc_id 或公布号是否为中国专利（以 CN 开头）。"""
        if not doc_id:
            return False
        return bool(re.match(r'^CN', doc_id.strip(), re.IGNORECASE))

    async def _find_cn_in_patent_family(self, doc_id: str, page=None) -> str | None:
        """在专利族标签页中查找 CN 开头的专利号。

        仅当 doc_id 非 CN 专利时才执行查找。返回找到的第一个 CN 专利号
        （docId 格式，如 CN116110953），未找到返回 None。

        查找策略：
        1. 先尝试点击「专利族」tab（尝试多种 href 关键词）
        2. 在面板中查找 CN 开头的专利号文本
        3. 同时尝试查找可点击的 CN 专利链接提取 docId
        """
        # 已经是 CN 专利，无需查找
        if self._is_cn_patent(doc_id):
            return None

        pg = page or self.page

        # 尝试多种专利族 tab 关键词
        family_text = ""
        for keyword in ("patentFamily", "family", "FAMILY", "simpleFamily",
                         "PatentFamily", "PATENT_FAMILY"):
            family_text = await self._click_and_extract_tab(keyword, page=pg)
            if family_text:
                break

        if not family_text:
            return None

        # 从专利族文本中提取 CN 专利号
        cn_matches = []
        # 模式1: CN + 7-13位数字 + 可选字母数字后缀（如 CN116110953A）
        for m in re.finditer(r'\b(CN\d{7,13}[A-Z]?\d*)\b', family_text, re.IGNORECASE):
            cn_num = m.group(1).upper()
            # 去掉末尾种类码（A/B/U等）得到 docId 格式
            cn_doc_id = re.sub(r'[ABU]\d?$', '', cn_num)
            if cn_doc_id not in cn_matches:
                cn_matches.append(cn_doc_id)

        if cn_matches:
            return cn_matches[0]

        # 模式2: 从链接中提取（兜底）
        try:
            cn_links = await pg.evaluate('''() => {
                var results = [];
                var links = document.querySelectorAll(
                    ".ui-tabs-panel a[href*='docId=']");
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].href || "";
                    var m = href.match(/docId=(CN\\d+)/i);
                    if (m) {
                        // 去掉末尾种类码
                        var cn = m[1].replace(/[ABU]\\d?$/, '');
                        if (results.indexOf(cn) === -1) results.push(cn);
                    }
                }
                return results;
            }''')
            if cn_links and len(cn_links) > 0:
                return cn_links[0]
        except Exception:
            pass

        return None


# ── 缓存验证 ──────────────────────────────────────────────────────

_INVALID_TITLES = {"", "(54)发明名称", "(54)", "发明名称", "无标题"}

def is_cached_patent_valid(data: dict) -> bool:
    """验证缓存的专利 JSON 是否包含有效内容"""
    try:
        # 检查抓取状态
        if data.get("fetch_status") == "failed":
            return False
        title = (data.get("title") or "").strip()
        claims = (data.get("claims") or "").strip()
        pub = (data.get("publication_number") or "").strip()
        # 必须有公布号 + 有效标题 + 权利要求内容
        return bool(pub and title not in _INVALID_TITLES and len(claims) > 20)
    except Exception:
        return False


# ── WO 全文切分工具 ──────────────────────────────────────────────────────

def _safe_filename(doc_id: str) -> str:
    """将 doc_id 转为安全的文件名, 替换斜杠、冒号等非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', '_', doc_id)


def _extract_wo_claims(fulltext: str) -> str:
    """从全文文本中提取权利要求书正文。

    支持多种格式：
    - CN: [权利要求 1] ... / 权利要求书
    - WO/EN: [Claim 1] ... / Claims
    - JP: [請求項1] ... / 請求の範囲
    - KR: [청구항 1] ... / 청구범위
    - US: Claims\\n1. ... (无方括号)
    """
    # 0. 方括号/方角括号标记最可靠（不限语言）：直接找第一个
    #    [Claim N]/[請求項 N]/[权利要求 N]/[청구항 N]（KR 常用【청구항 N】）
    #    并取其到结尾。WO/JP 的 claims 目录只是纯数字，不会误命中。
    m = re.search(
        r'[\[【](?:Claim|請求項|权利要求|청구항)\s*\d+\s*[\]】]',
        fulltext, re.IGNORECASE)
    if m:
        return fulltext[m.start():].strip()

    # 1. 中文格式：权利要求书 → [权利要求 N]
    idx = fulltext.rfind('权利要求书')
    if idx < 0:
        idx = fulltext.rfind('权利要求')
    if idx >= 0:
        tail = fulltext[idx:]
        m = re.search(r'\[权利要求\s*\d+\][\s\S]*', tail)
        if m:
            return m.group(0).strip()
        # 中文无方括号格式
        m = re.search(r'权利要求\s*\n\s*\d+[\.\s、][\s\S]*', tail)
        if m:
            return m.group(0).strip()

    # 2. 编号格式（美/德/法）：Claims/Patentansprüche/Revendications
    #    标题后紧跟 [Claim 1] 或编号 "1." 等（无方括号的 US/DE/FR 格式）
    for pattern in [
        r'(?:Claims?|Patentansprüche|Revendications?)'
        r'\s*\n\s*\[Claim\s*\d+\]',
        r'(?:Claims?|Patentansprüche|Revendications?)'
        r'\s*\n\s*\d+\s*[\.、]\s*\S',
    ]:
        m = re.search(pattern, fulltext, re.IGNORECASE)
        if m:
            tail = fulltext[m.start():]
            m2 = re.search(r'\[Claim\s*\d+\][\s\S]*', tail, re.IGNORECASE)
            if m2:
                return m2.group(0).strip()
            return tail.strip()
    return ""


def _extract_wo_description(fulltext: str) -> str:
    """从 WO 全文文本中提取说明书正文。

    FULLTEXT 面板 = 书目 → 目录 → 正文 → 权利要求 → 摘要 → 附图。
    「技术领域」等章节标题在目录里也会出现一次，因此取最后一次出现的
    位置作为正文起点，避开目录。
    """
    # 正文起点：最后一次出现的章节标题（多语言）
    starts = []
    for pat in (r'技术领域', r'技術分野', r'Technical\s*Field',
                r'Field of the (?:present\s+)?[Ii]nvention',
                r'Technisches?\s+Gebiet', r'Domaine\s+de\s+la\s+technique',
                r'기술분야'):
        for m in re.finditer(pat, fulltext, re.IGNORECASE):
            starts.append(m.start())
    if not starts:
        # 无章节标题 → 用第一个正文段落编号 [NNNN] 起头（某些格式）
        m = re.search(r'\[\s*\d{3,4}\s*\]', fulltext)
        if m:
            starts = [m.start()]
    if not starts:
        # 结构性兜底：取 claims 起点之前的文本（跳过开头导航/书目垃圾）
        return _desc_before_claims(fulltext)
    start = max(starts)

    # 终点：start 之后第一个 权利要求书/請求の範囲/Claims/청구범위 标题
    end = len(fulltext)
    for pat in (r'权利要求书', r'請求の範囲', r'Claims?', r'청구범위'):
        for m in re.finditer(pat, fulltext, re.IGNORECASE):
            if m.start() > start and m.start() < end:
                end = m.start()
    return fulltext[start:end].strip()


def _desc_before_claims(fulltext: str) -> str:
    """无章节标题/段落编号时，从 claims 起点之前的文本取说明书正文。

    跳过开头的 WIPO 导航/书目垃圾（最后一个 Document/明細書 标记之后）。
    """
    claims_m = re.search(
        r'[\[【](?:Claim|請求項|权利要求|청구항)\s*\d+\s*[\]】]',
        fulltext, re.IGNORECASE)
    if not claims_m:
        return ""
    head = fulltext[:claims_m.start()]
    # 去掉头部导航垃圾：取最后一个文档起始标记之后
    idx = -1
    for mark in (r'Document', r'明\s*細\s*書', r'明細書', r'Description'):
        for m in re.finditer(mark, head):
            idx = max(idx, m.end())
    if idx > 0:
        head = head[idx:]
    return head.strip()
