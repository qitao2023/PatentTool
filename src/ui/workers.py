"""
后台工作线程 - 所有耗时操作在 QThread 中执行
"""
import asyncio
import random

from PySide6.QtCore import QThread, Signal

from src.utils.signals import WorkerSignals
from src.utils.config import Settings
from src.utils.prompts import (
    load_prompt,
    render_template,
    FINAL_REVIEW_FALLBACK_SYSTEM_PROMPT,
    FINAL_REVIEW_FALLBACK_USER_PROMPT,
)


class PDFParseWorker(QThread):
    """PDF解析后台线程"""

    def __init__(self, pdf_path: str, settings: Settings, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path
        self.settings = settings
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.progress.emit(10, "正在解析PDF...")
            self.signals.log.emit("INFO", f"开始解析PDF: {self.pdf_path}")

            from src.pdf_extractor.extractor import PatentPDFExtractor
            extractor = PatentPDFExtractor(self.pdf_path)
            patent = extractor.extract()

            self.signals.log.emit("SUCCESS",
                f"PDF解析完成: {patent.title} | {len(patent.claims)}项权利要求")
            self.signals.progress.emit(30, "PDF解析完成")
            self.signals.pdf_done.emit(patent)
        except Exception as e:
            self.signals.error.emit(f"PDF解析失败: {e}")
            self.signals.log.emit("ERROR", f"PDF解析失败: {e}")
            self.signals.finished.emit(False, str(e))


class ApplicationDateWorker(QThread):
    """申请日轻量提取后台线程：只读 PDF 前两页，不触发全流程。

    用于：选择 PDF 后自动提取、点「提取」按钮手动重试。
    提取不到只发空串，不视为异常（扫描件/无文字层属正常情况）。
    """

    extracted = Signal(str)   # 提取到的申请日；空串 = 未提取到
    error = Signal(str)       # PDF 打不开等真实异常

    def __init__(self, pdf_path: str, parent=None):
        super().__init__(parent)
        self.pdf_path = pdf_path

    def run(self):
        try:
            from src.pdf_extractor.extractor import extract_application_date
            date = extract_application_date(self.pdf_path)
            self.extracted.emit(date)
        except Exception as e:
            self.error.emit(f"提取申请日失败: {e}")


class QueryGenerateWorker(QThread):
    """检索式生成后台线程"""

    def __init__(self, patent_doc, settings: Settings,
                 ai_provider: str | None = None,
                 max_queries: int | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.settings = settings
        self.ai_provider = ai_provider
        self._max_queries = max_queries
        self.signals = WorkerSignals()

    def run(self):
        try:
            max_q = self._max_queries or self.settings.query_max_queries
            self.signals.progress.emit(30, f"正在生成 {max_q} 个检索式...")
            self.signals.log.emit("INFO", f"调用 {self.ai_provider or '默认AI'} 生成 {max_q} 个检索式...")

            from src.query_generator.generator import QueryGenerator
            generator = QueryGenerator(self.settings, provider=self.ai_provider)
            queries = generator.generate(self.patent_doc, max_queries=max_q)

            self.signals.log.emit("SUCCESS", f"生成 {len(queries)} 个检索式")
            for i, q in enumerate(queries, 1):
                self.signals.log.emit("INFO",
                    f"  检索式{i} [{q.get('search_angle','')}]: {q.get('query_string','')}")
            self.signals.progress.emit(45, "检索式生成完成")
            self.signals.queries_done.emit(queries)
        except Exception as e:
            self.signals.error.emit(f"检索式生成失败: {e}")
            self.signals.log.emit("ERROR", f"检索式生成失败: {e}")
            self.signals.finished.emit(False, str(e))


class PatentscopeSearchAndFetchWorker(QThread):
    """
    PATENTSCOPE 检索 + 智能筛选 Worker。

    流程：
      阶段1: 搜索摘要（快，200 条/检索式）
      阶段2: 结果超过下载上限时降量（引用优先 + 轮询配额，不做 AI 粗筛）
      阶段3: 并行下载完整详情 → 02_patent_details/
      阶段4: 全量 Claims 广筛（只发权要/实施方式，分批评分排序）
    """

    def __init__(self, queries: list, settings: Settings,
                 patent_doc=None, max_fetch: int = 200,
                 top_n: int = 25, output_dir=None,
                 cache_dir: str | None = None,
                 stop_after: str = "full",
                 include_citations: bool = True, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.patent_doc = patent_doc
        self.max_fetch = max_fetch
        self._given_output_dir = output_dir
        self._cache_dir = cache_dir
        self.stop_after = stop_after
        self.include_citations = include_citations
        self.signals = WorkerSignals()
        self._is_running = True
        self.output_dir = None

    def stop(self):
        self._is_running = False
        from src.web_automation.browser_manager import BrowserManager
        BrowserManager.cancel_cooldown()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"PATENTSCOPE检索失败: {e}")
            self.signals.log.emit("ERROR", f"PATENTSCOPE检索失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        from pathlib import Path
        import json as json_module
        from datetime import datetime
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.human_behavior import HumanBehavior
        from src.web_automation.patentscope_scraper import (
            PatentscopeScraper, is_cached_patent_valid, _safe_filename)
        from src.analysis.screener import PatentScreener

        # ── 输出目录 ──────────────────────────────────────────────
        if self._given_output_dir:
            self.output_dir = Path(self._given_output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
        else:
            from src.utils.paths import normalize_patent_number
            pname = "unknown"
            if self.patent_doc:
                pname = normalize_patent_number(
                    self.patent_doc.publication_number
                    or self.patent_doc.title or "unknown"
                )
            run_dir = f"{pname}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            self.output_dir = Path.cwd() / "data" / "output" / run_dir
            self.output_dir.mkdir(parents=True, exist_ok=True)
        output_dir = self.output_dir

        max_detail = self.max_fetch      # 全文下载上限

        # ================================================================
        # 阶段1: 搜索摘要
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        eng_name = ("Google Patents" if self.settings.search_source == "google"
                    else "PATENTSCOPE (WIPO)")
        self.signals.log.emit("INFO", "阶段1: 搜索摘要")
        self.signals.log.emit("SUCCESS", f"检索引擎: {eng_name}")
        self.signals.progress.emit(5, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)
        if self.settings.search_source == "google":
            self.signals.log.emit("SUCCESS", "浏览器就绪 — Google Patents")
        else:
            self.signals.log.emit("SUCCESS", "浏览器就绪 — PATENTSCOPE 无需登录")

        human = HumanBehavior(self.settings)
        scraper = PatentscopeScraper(page, self.settings, human)

        # ── 分离精准检索式与宽泛兜底检索式 ──
        # 宽泛兜底仅在精准检索结果为零时才执行
        def _is_fallback(q: dict) -> bool:
            return (q.get("priority") == 99 or
                    str(q.get("search_angle", "")).startswith("【宽泛兜底】"))

        normal_queries = [q for q in self.queries if not _is_fallback(q)]
        fallback_queries = [q for q in self.queries if _is_fallback(q)]

        if fallback_queries:
            self.signals.log.emit("INFO",
                f"检索式: {len(normal_queries)} 个精准 + "
                f"{len(fallback_queries)} 个宽泛兜底（仅零结果时触发）")

        all_abstracts = []
        MAX_SEARCH_RETRIES = 3

        if self.settings.search_source == "google" and normal_queries:
            # ══ Google：并行搜索全部检索式（多标签页）══
            from src.web_automation.google_patents import (
                search_abstracts_parallel as gsearch_parallel)
            q_strings = [q.get("query_string", "").strip() for q in normal_queries]
            self.signals.log.emit("INFO",
                f"阶段1: 并行搜索 {len(normal_queries)} 个检索式 "
                f"(并发 {self.settings.search_search_concurrency})...")
            self.signals.progress.emit(5, "阶段1: 并行搜索...")

            per_query = await gsearch_parallel(
                page, q_strings,
                max_results=self.settings.patentscope_max_results,
                signals=self.signals,
                concurrency=self.settings.search_search_concurrency)

            # ── 失败的检索式：切浏览器 + 冷却 + 重试（最多3轮）──
            failed_idx = [i for i, r in enumerate(per_query) if r.get("error")]
            retry_round = 0
            while failed_idx and retry_round < MAX_SEARCH_RETRIES and self._is_running:
                retry_round += 1
                err_text = "\n".join(per_query[i]["error"] or "" for i in failed_idx)
                is_403 = ("403" in err_text
                          and ("Forbidden" in err_text or "FORBIDDEN" in err_text))
                # 网络中断类错误：NS_ERROR_NET_INTERRUPT / RESET / TIMEOUT 等
                is_net_error = any(kw in err_text for kw in (
                    "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                    "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                    "NS_BINDING_ABORTED", "net::ERR_",
                    "NS_ERROR_PROXY_CONNECTION_REFUSED",
                    "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                ))
                if not (is_403 or is_net_error):
                    break  # 非限流错误，重试无意义
                if is_403:
                    new_browser = BrowserManager.switch_channel(on_403=True)
                    cool = random.uniform(8, 20)
                    reason = "403"
                else:
                    new_browser = BrowserManager.switch_channel()
                    cool = random.uniform(3, 8)
                    reason = "网络中断"
                self.signals.log.emit("WARN",
                    f"  并行搜索 {len(failed_idx)} 个检索式遇 {reason}，"
                    f"切换至 {new_browser} + 冷却 {cool:.0f}s "
                    f"(第 {retry_round}/{MAX_SEARCH_RETRIES} 轮重试)...")
                await browser_mgr.close()
                await asyncio.sleep(cool)
                try:
                    context, page = await browser_mgr.launch_with_retry(max_retries=1)
                except Exception:
                    await asyncio.sleep(2)
                    context, page = await browser_mgr.launch_with_retry(max_retries=1)
                human = HumanBehavior(self.settings)
                scraper = PatentscopeScraper(page, self.settings, human)
                retry_res = await gsearch_parallel(
                    page, [q_strings[i] for i in failed_idx],
                    max_results=self.settings.patentscope_max_results,
                    signals=self.signals,
                    concurrency=min(self.settings.search_search_concurrency,
                                    len(failed_idx)))
                for k, i in enumerate(failed_idx):
                    per_query[i] = retry_res[k]
                failed_idx = [i for i in failed_idx if per_query[i].get("error")]

            # ── 收尾：逐式存盘 + 日志 ──
            for idx, query in enumerate(normal_queries):
                if not self._is_running:
                    break
                q_str = query.get("query_string", "")
                angle = query.get("search_angle", "")
                res = per_query[idx]
                abstracts = res.get("abstracts", [])
                error_msg = res.get("error")
                for a in abstracts:
                    a["source_query"] = q_str
                all_abstracts.append(abstracts)
                self._save_json(
                    output_dir / f"01_query_{idx+1:02d}_abstracts.json",
                    {"query": q_str, "search_angle": angle,
                     "count": len(abstracts), "error": error_msg,
                     "results": abstracts})
                if error_msg:
                    self.signals.log.emit("ERROR",
                        f"  检索式{idx+1} 重试 {MAX_SEARCH_RETRIES} 次仍失败: {error_msg}")
                else:
                    self.signals.log.emit("SUCCESS",
                        f"检索式{idx+1}: 获取 {len(abstracts)} 篇摘要")
            self.signals.progress.emit(20, "阶段1: 搜索完成")
        else:
            # ══ WIPO / 单检索式：串行（原逻辑）══
            for idx, query in enumerate(normal_queries):
                if not self._is_running:
                    break
                q_str = query.get("query_string", "")
                angle = query.get("search_angle", "")
                self.signals.log.emit("INFO",
                    f"检索式 {idx+1}/{len(normal_queries)} [{angle}]: {q_str}")

                progress = 5 + int((idx + 1) / len(normal_queries) * 15)
                self.signals.progress.emit(progress,
                    f"阶段1: 搜索 {idx+1}/{len(normal_queries)}")

                # ── 带 403 / 网络错误 重试的搜索 ──
                abstracts = []
                for attempt in range(1, MAX_SEARCH_RETRIES + 1):
                    try:
                        abstracts = await scraper.search_abstracts(
                            q_str, max_results=self.settings.patentscope_max_results,
                            signals=self.signals)
                        break  # 成功，跳出重试循环
                    except Exception as e:
                        err_msg = str(e)
                        is_403 = "403" in err_msg and ("Forbidden" in err_msg or "FORBIDDEN" in err_msg)
                        # 网络中断类错误：NS_ERROR_NET_INTERRUPT / RESET / TIMEOUT / CONNECTION_REFUSED 等
                        is_net_error = any(kw in err_msg for kw in (
                            "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                            "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                            "NS_BINDING_ABORTED", "net::ERR_",
                            "NS_ERROR_PROXY_CONNECTION_REFUSED",
                            "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                        ))
                        can_retry = (is_403 or is_net_error) and attempt < MAX_SEARCH_RETRIES and self._is_running
                        if can_retry:
                            # 切换浏览器 + 冷却 + 重试
                            if is_403:
                                new_browser = BrowserManager.switch_channel(on_403=True)
                                cool = random.uniform(8, 20)
                                reason = "403"
                            else:
                                new_browser = BrowserManager.switch_channel()
                                cool = random.uniform(3, 8)
                                reason = "网络中断"
                            self.signals.log.emit("WARN",
                                f"  搜索遇到 {reason}，切换至 {new_browser} + 冷却 {cool:.0f}s "
                                f"(第 {attempt}/{MAX_SEARCH_RETRIES} 次重试)...")
                            await browser_mgr.close()
                            await asyncio.sleep(cool)
                            try:
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            except Exception:
                                await asyncio.sleep(2)
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            human = HumanBehavior(self.settings)
                            scraper = PatentscopeScraper(page, self.settings, human)
                        else:
                            raise  # 非403/网络错误，或重试耗尽，向外抛出

                if not abstracts and attempt >= MAX_SEARCH_RETRIES:
                    self.signals.log.emit("ERROR",
                        f"  检索式{idx+1} 重试 {MAX_SEARCH_RETRIES} 次仍失败，跳过")
                    continue

                for a in abstracts:
                    a["source_query"] = q_str
                all_abstracts.append(abstracts)

                self.signals.log.emit("SUCCESS",
                    f"检索式{idx+1}: 获取 {len(abstracts)} 篇摘要")
                # 每条检索式结果单独存盘
                self._save_json(
                    output_dir / f"01_query_{idx+1:02d}_abstracts.json",
                    {"query": q_str, "search_angle": angle,
                     "count": len(abstracts), "results": abstracts})

                if idx < len(normal_queries) - 1 and self._is_running:
                    await human.inter_search_delay(idx + 1)

        # 轮询合并去重：从每个检索式轮流取，保证各检索式均匀贡献
        seen = set()
        unique_abstracts = []
        max_batch_len = max((len(b) for b in all_abstracts), default=0)
        for i in range(max_batch_len):
            for batch in all_abstracts:
                if i < len(batch):
                    a = batch[i]
                    key = a.get("publication_number") or a.get("doc_id", "")
                    if key and key not in seen:
                        seen.add(key)
                        unique_abstracts.append(a)

        # 过滤掉目标专利自身
        unique_abstracts, self_filtered = self._filter_self_patent(unique_abstracts)
        if self_filtered > 0:
            self.signals.log.emit("INFO",
                f"  已排除本申请自身: {self_filtered} 篇")

        total_abstracts = len(unique_abstracts)
        self.signals.log.emit("SUCCESS",
            f"阶段1 完成: 去重后 {total_abstracts} 篇摘要")

        # 保存阶段1
        stage1_path = output_dir / "01_search_abstracts.json"
        self._save_json(stage1_path, {
            "stage": "search_abstracts",
            "timestamp": datetime.now().isoformat(),
            "total": total_abstracts,
            "queries": [q.get("query_string", "") for q in self.queries],
            "results": unique_abstracts,
        })
        self.signals.log.emit("INFO", f"  已保存: {stage1_path}")

        await browser_mgr.close()

        # ── 0结果兜底：使用 AI 预生成的宽泛检索式 ──────────────────
        if total_abstracts == 0 and fallback_queries:
            self.signals.log.emit("INFO",
                f"精准检索式无结果，使用 {len(fallback_queries)} 个宽泛兜底检索式重试...")
            self.signals.progress.emit(5, "宽泛兜底检索...")
            fallback_abstracts = []
            browser_mgr = BrowserManager(self.settings)
            context, page = await browser_mgr.launch_with_retry(max_retries=1)
            human = HumanBehavior(self.settings)
            scraper = PatentscopeScraper(page, self.settings, human)
            for fq in fallback_queries:
                if not self._is_running:
                    break
                fq_str = fq.get("query_string", "")

                # ── 带 403 / 网络错误 重试的兜底搜索 ──
                abstracts = []
                for attempt in range(1, 4):
                    try:
                        if self.settings.search_source == "google":
                            from src.web_automation.google_patents import search_abstracts as gsearch
                            abstracts = await gsearch(
                                page, fq_str,
                                max_results=self.settings.patentscope_max_results,
                                signals=self.signals)
                        else:
                            abstracts = await scraper.search_abstracts(
                                fq_str, max_results=self.settings.patentscope_max_results,
                                signals=self.signals)
                        break
                    except Exception as e:
                        err_msg = str(e)
                        is_403 = "403" in err_msg and ("Forbidden" in err_msg or "FORBIDDEN" in err_msg)
                        is_net_error = any(kw in err_msg for kw in (
                            "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                            "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                            "NS_BINDING_ABORTED", "net::ERR_",
                            "NS_ERROR_PROXY_CONNECTION_REFUSED",
                            "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                        ))
                        can_retry = (is_403 or is_net_error) and attempt < 3 and self._is_running
                        if can_retry:
                            from src.web_automation.browser_manager import BrowserManager
                            if is_403:
                                new_browser = BrowserManager.switch_channel(on_403=True)
                                cool = random.uniform(8, 20)
                                reason = "403"
                            else:
                                new_browser = BrowserManager.switch_channel()
                                cool = random.uniform(3, 8)
                                reason = "网络中断"
                            self.signals.log.emit("WARN",
                                f"  兜底搜索 {reason}，切换 {new_browser} + 冷却 {cool:.0f}s "
                                f"(第 {attempt}/3 次重试)...")
                            await browser_mgr.close()
                            await asyncio.sleep(cool)
                            try:
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            except Exception:
                                await asyncio.sleep(2)
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            human = HumanBehavior(self.settings)
                            scraper = PatentscopeScraper(page, self.settings, human)
                        else:
                            raise

                if not abstracts and attempt >= 3:
                    self.signals.log.emit("WARN",
                        f"  兜底: {fq_str} → 重试3次仍失败")
                    continue
                for a in abstracts:
                    a["source_query"] = fq_str
                fallback_abstracts.append(abstracts)
                self.signals.log.emit("INFO",
                    f"  兜底: {fq_str} → {len(abstracts)} 篇")
            await browser_mgr.close()
            seen = set()
            unique_abstracts = []
            for batch in fallback_abstracts:
                for a in batch:
                    key = a.get("publication_number") or a.get("doc_id", "")
                    if key and key not in seen:
                        seen.add(key)
                        unique_abstracts.append(a)
            unique_abstracts, _ = self._filter_self_patent(unique_abstracts)
            total_abstracts = len(unique_abstracts)
            self.signals.log.emit("INFO",
                f"  兜底结果: {total_abstracts} 篇摘要")

        # ── 日期淘汰（下载前主力过滤）：公开日 ≥ 本申请截止日的直接剔除 ──
        unique_abstracts = self._apply_date_filter(unique_abstracts, "阶段1 搜索摘要")
        total_abstracts = len(unique_abstracts)
        # 同步落盘，保证 01_search_abstracts.json 与过滤后的流水线一致
        self._save_json(stage1_path, {
            "stage": "search_abstracts",
            "timestamp": datetime.now().isoformat(),
            "total": total_abstracts,
            "queries": [q.get("query_string", "") for q in self.queries],
            "results": unique_abstracts,
        })

        if total_abstracts == 0:
            self.signals.log.emit("WARN",
                "无可用的对比文件（检索无结果或全部被日期淘汰），停止检索")
            self.signals.finished.emit(True, "无结果")
            return

        self.signals.query_complete.emit(1, 1, unique_abstracts)

        # ── 断点：搜索完摘要就停 ──────────────────────────────────────
        if self.stop_after == "abstracts":
            self._stop_here("搜索摘要", [unique_abstracts], stage1_path)
            return

        # ── 从说明书提取引用专利 ─────────────────────────────────────
        if self.include_citations and self.patent_doc and self.patent_doc.description:
            cited_pubs = _extract_cited_patent_numbers(self.patent_doc.description)
            # 排除目标专利自身
            target_pn = (self.patent_doc.publication_number or "").replace(" ", "")
            cited_pubs = [p for p in cited_pubs if _normalize_pn(p) != _normalize_pn(target_pn)]
            # 排除已在搜索结果中的
            existing_pubs = {_normalize_pn(a.get("publication_number", "")) for a in unique_abstracts}
            new_pubs = [p for p in cited_pubs if _normalize_pn(p) not in existing_pubs]
            new_pubs = list(dict.fromkeys(new_pubs))  # 去重保序

            if new_pubs:
                self.signals.log.emit("INFO", "=" * 40)
                self.signals.log.emit("INFO",
                    f"从说明书提取引用专利: 共 {len(cited_pubs)} 个, "
                    f"新增 {len(new_pubs)} 个, 开始下载...")
                self.signals.progress.emit(28, f"下载引用专利 ({len(new_pubs)}个)...")

                cache_dir = Path(self._cache_dir) if self._cache_dir else output_dir / "02_patent_details"
                cache_dir.mkdir(parents=True, exist_ok=True)

                browser_mgr1 = BrowserManager(self.settings)
                context1, page1 = await browser_mgr1.launch_with_retry(max_retries=2)
                human1 = HumanBehavior(self.settings)
                scraper1 = PatentscopeScraper(page1, self.settings, human1)

                added = 0
                _cite_failed = []
                _consecutive_403 = 0

                async def _restart_for_cite(is_403: bool = True):
                    """遇到403或网络错误 → 切换浏览器 + 冷却 + 重启"""
                    nonlocal context1, page1, scraper1, human1, browser_mgr1, _consecutive_403
                    from src.web_automation.browser_manager import BrowserManager
                    if is_403:
                        new_browser = BrowserManager.switch_channel(on_403=True)
                        cool = random.uniform(8, 20)
                        reason = "403"
                    else:
                        new_browser = BrowserManager.switch_channel()
                        cool = random.uniform(3, 8)
                        reason = "网络中断"
                    self.signals.log.emit("WARN",
                        f"  引用下载 {reason}，切换至 {new_browser} + 冷却 {cool:.0f}s...")
                    await browser_mgr1.close()
                    await asyncio.sleep(cool)
                    browser_mgr1 = BrowserManager(self.settings)
                    try:
                        context1, page1 = await browser_mgr1.launch_with_retry(max_retries=1)
                    except Exception:
                        await asyncio.sleep(2)
                        context1, page1 = await browser_mgr1.launch_with_retry(max_retries=1)
                    human1 = HumanBehavior(self.settings)
                    scraper1 = PatentscopeScraper(page1, self.settings, human1)
                    if is_403:
                        _consecutive_403 = 0

                for i, pub in enumerate(new_pubs):
                    if not self._is_running:
                        break
                    # 检查缓存
                    safe = _safe_filename(pub)
                    cache_path = cache_dir / f"{safe}.json"
                    if cache_path.exists():
                        try:
                            existing = json_module.loads(cache_path.read_text(encoding="utf-8"))
                            if is_cached_patent_valid(existing):
                                item = {
                                    "doc_id": existing.get("doc_id", pub),
                                    "publication_number": existing.get("publication_number", pub),
                                    "title": existing.get("title", ""),
                                    "abstract_snippet": (existing.get("abstract") or "")[:300],
                                    "ipc": existing.get("ipc", ""),
                                    "applicant": existing.get("applicant", ""),
                                    "publication_date": existing.get("publication_date", ""),
                                    "source_query": f"说明书引用: {pub}",
                                }
                                unique_abstracts.append(item)
                                added += 1
                                continue
                        except Exception:
                            pass

                    # 缓存未命中 → 联网获取（带403重试）
                    self.signals.log.emit("INFO",
                        f"  下载引用专利 [{i+1}/{len(new_pubs)}]: {pub}")
                    success = False
                    for attempt in range(1, 4):
                        try:
                            _consecutive_403 = 0
                            detail = await scraper1.fetch_detail(pub)
                            if detail:
                                cache_path.write_text(
                                    json_module.dumps(detail, indent=2, ensure_ascii=False, default=str),
                                    encoding="utf-8")
                                item = {
                                    "doc_id": detail.get("doc_id", pub),
                                    "publication_number": detail.get("publication_number", pub),
                                    "title": detail.get("title", ""),
                                    "abstract_snippet": (detail.get("abstract") or "")[:300],
                                    "ipc": detail.get("ipc", ""),
                                    "applicant": detail.get("applicant", ""),
                                    "publication_date": detail.get("publication_date", ""),
                                    "source_query": f"说明书引用: {pub}",
                                }
                                unique_abstracts.append(item)
                                added += 1
                                success = True
                            break
                        except Exception as e:
                            err_msg = str(e)
                            is_403 = "403" in err_msg and ("Forbidden" in err_msg or "FORBIDDEN" in err_msg)
                            is_net_error = any(kw in err_msg for kw in (
                                "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                                "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                                "NS_BINDING_ABORTED", "net::ERR_",
                                "NS_ERROR_PROXY_CONNECTION_REFUSED",
                                "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                            ))
                            if (is_403 or is_net_error) and attempt < 3 and self._is_running:
                                if is_403:
                                    _consecutive_403 += 1
                                await _restart_for_cite(is_403=is_403)
                            else:
                                if attempt >= 3:
                                    self.signals.log.emit("WARN",
                                        f"    下载失败(重试3次): {pub} - {e}")
                                else:
                                    self.signals.log.emit("WARN", f"    下载失败: {pub} - {e}")
                                break
                    if not success:
                        _cite_failed.append(pub)
                    await asyncio.sleep(1.0)

                # ── 引用专利补下载 ──
                if _cite_failed and self._is_running:
                    self.signals.log.emit("INFO",
                        f"  === 补下载 {len(_cite_failed)} 篇引用专利 === ")
                    await _restart_for_cite(is_403=False)
                    await asyncio.sleep(3)
                    for pub in _cite_failed:
                        if not self._is_running:
                            break
                        self.signals.log.emit("INFO", f"  重试: {pub}")
                        success = False
                        for attempt in range(1, 4):
                            try:
                                detail = await scraper1.fetch_detail(pub)
                                if detail:
                                    cache_path = cache_dir / f"{_safe_filename(pub)}.json"
                                    cache_path.write_text(
                                        json_module.dumps(detail, indent=2, ensure_ascii=False, default=str),
                                        encoding="utf-8")
                                    item = {
                                        "doc_id": detail.get("doc_id", pub),
                                        "publication_number": detail.get("publication_number", pub),
                                        "title": detail.get("title", ""),
                                        "abstract_snippet": (detail.get("abstract") or "")[:300],
                                        "ipc": detail.get("ipc", ""),
                                        "applicant": detail.get("applicant", ""),
                                        "source_query": f"说明书引用: {pub}",
                                    }
                                    unique_abstracts.append(item)
                                    added += 1
                                    success = True
                                break
                            except Exception as e:
                                err_msg = str(e)
                                is_403 = "403" in err_msg and ("Forbidden" in err_msg or "FORBIDDEN" in err_msg)
                                is_net_error = any(kw in err_msg for kw in (
                                    "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                                    "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                                    "NS_BINDING_ABORTED", "net::ERR_",
                                    "NS_ERROR_PROXY_CONNECTION_REFUSED",
                                    "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                                ))
                                if (is_403 or is_net_error) and attempt < 3 and self._is_running:
                                    await _restart_for_cite(is_403=is_403)
                                else:
                                    self.signals.log.emit("WARN",
                                        f"    重试仍失败: {pub} - {e}")
                                    break
                        if not success:
                            self.signals.log.emit("WARN", f"    最终失败: {pub}")
                        await asyncio.sleep(3.0)

                await browser_mgr1.close()
                self.signals.log.emit("SUCCESS",
                    f"引用专利下载完成: 新增 {added} 篇, 累计 {len(unique_abstracts)} 篇")
                total_abstracts = len(unique_abstracts)

        # ── 日期淘汰（含说明书引用追加的对比文件）──
        unique_abstracts = self._apply_date_filter(unique_abstracts, "阶段2 下载前")
        total_abstracts = len(unique_abstracts)

        # ================================================================
        # 阶段2: 结果超过下载上限时降量
        #   策略: 说明书引用专利优先保留（申请人在说明书中明确引用的
        #         对比文件，相关度最高，不能被截断丢掉）
        #        + 剩余名额按检索式轮询配额填满
        #         （unique_abstracts 已按轮询合并，截取前 N 即每式等额
        #          配额：20式×100→选1000=每式约50，不足的式子让出名额）
        #   不做 AI 摘要粗筛：全量下载后再用 Claims 广筛，避免摘要漏检
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        if total_abstracts > max_detail:
            cited = [a for a in unique_abstracts
                     if str(a.get("source_query", "")).startswith("说明书引用")]
            search_part = [a for a in unique_abstracts
                           if not str(a.get("source_query", "")).startswith("说明书引用")]
            keep_cited = cited[:max_detail]
            budget = max_detail - len(keep_cited)
            if budget > 0:
                to_fetch = keep_cited + search_part[:budget]
            else:
                to_fetch = keep_cited
            self.signals.log.emit("INFO",
                f"阶段2: 结果数 {total_abstracts} > 下载上限 {max_detail}，降量选取 "
                f"{len(to_fetch)} 篇 = 引用专利优先 {len(keep_cited)} "
                f"+ 检索式轮询配额 {len(to_fetch) - len(keep_cited)}")
        else:
            self.signals.log.emit("INFO",
                f"阶段2: 结果数 {total_abstracts} ≤ 上限 {max_detail}，全部下载全文")
            to_fetch = unique_abstracts

        # ── 断点：截断选择后（下载前）──
        if self.stop_after == "screen":
            self._stop_here("截断选择后", [to_fetch])
            return

        # ================================================================
        # 阶段3: 串行下载完整详情 → 共享缓存
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO",
            f"阶段3: 下载 {len(to_fetch)} 篇完整详情 → {self._cache_dir or 'output'}")
        self.signals.progress.emit(30, "阶段3: 启动浏览器下载...")

        details_dir = Path(self._cache_dir) if self._cache_dir else output_dir / "02_patent_details"

        browser_mgr2 = BrowserManager(self.settings)
        context2, page2 = await browser_mgr2.launch_with_retry(max_retries=2)
        human2 = HumanBehavior(self.settings)
        scraper2 = PatentscopeScraper(page2, self.settings, human2)

        # 下载并发：用设置值 search.download_concurrency；wipo 保守防403
        dl_conc = self.settings.search_download_concurrency
        if self.settings.search_source == "wipo":
            dl_conc = min(dl_conc, 1)
        await scraper2.fetch_details_parallel(
            to_fetch, str(details_dir), concurrency=dl_conc, signals=self.signals)

        await browser_mgr2.close()

        # ── CN同族替换去重：移除因CN替换产生的重复 ──
        _cn_dup_removed = 0
        _cn_pubs = {}  # publication_number → file_path
        # 第一遍：收集所有 CN 专利（非替换的原始 CN 专利优先）
        for f in sorted(Path(details_dir).glob("*.json")):
            try:
                d = json_module.loads(f.read_text(encoding="utf-8"))
                if d.get("fetch_status") != "ok":
                    continue
                pub = (d.get("publication_number") or "").strip()
                if not pub:
                    continue
                is_substituted = bool(d.get("_cn_family_original"))
                if pub not in _cn_pubs:
                    _cn_pubs[pub] = (f, is_substituted)
                elif is_substituted and not _cn_pubs[pub][1]:
                    # 当前是替换来的，已有的是原生CN → 删除当前
                    f.unlink(missing_ok=True)
                    cn_orig = d.get("_cn_family_original", "?")
                    self.signals.log.emit("INFO",
                        f"  🗑 去重: CN同族替换 {cn_orig} → {pub}，"
                        f"已有原生CN专利，删除替换条目")
                    _cn_dup_removed += 1
                elif not is_substituted and _cn_pubs[pub][1]:
                    # 当前是原生CN，已有的是替换来的 → 删除已有的
                    old_f, _ = _cn_pubs[pub]
                    cn_orig = "?"
                    try:
                        old_d = json_module.loads(old_f.read_text(encoding="utf-8"))
                        cn_orig = old_d.get("_cn_family_original", cn_orig)
                    except Exception:
                        pass
                    old_f.unlink(missing_ok=True)
                    self.signals.log.emit("INFO",
                        f"  🗑 去重: 原生CN {pub} 保留，移除 CN同族替换 {cn_orig} → {pub}")
                    _cn_pubs[pub] = (f, False)
                    _cn_dup_removed += 1
            except Exception:
                pass

        if _cn_dup_removed > 0:
            self.signals.log.emit("SUCCESS",
                f"  CN同族去重完成: 移除 {_cn_dup_removed} 篇重复专利")
        # ── CN同族去重结束 ──

        # ── 日期淘汰二次校验：详情页权威公开日兜住搜索阶段漏网的 ──
        _cutoff = self._application_cutoff()
        if _cutoff is not None:
            _pruned = self._prune_eliminated_details(details_dir, _cutoff)
            if _pruned > 0:
                self.signals.log.emit("SUCCESS",
                    f"  二次校验淘汰 {_pruned} 篇（公开日 ≥ 截止日 {_cutoff}）")

        # ── 断点：下载后 ──
        if self.stop_after == "download":
            self._stop_here("下载对比文件", [to_fetch])
            return

        # ================================================================
        # 阶段4: 全量 Claims 广筛（只发权利要求书，分批评分排序）
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO", "阶段4: Claims 广筛...")
        self.signals.progress.emit(45, "阶段4: Claims 广筛...")

        # 历史记录库：已知对比文件复用历史评分，只对新出现的调 AI
        history = None
        _history_base = (Path(self._cache_dir).parent
                         if self._cache_dir else None)
        if (_history_base and self.patent_doc
                and getattr(self.patent_doc, "publication_number", None)):
            from src.analysis.history import ScreeningHistory
            history = ScreeningHistory(
                str(_history_base), self.patent_doc.publication_number)

        if self.patent_doc:
            screener2 = PatentScreener(self.settings)
            # screen_claims_all 内部按批创建独立 client 并写 ai_logs/batch_NN/
            all_scored = []
            if history is not None:
                # 分区：已有历史评分的直接复用；只有"本次有效新增"走 AI 广筛。
                # 本次有效新增 = 本次下载(to_fetch，≤下载上限)中未评分/未缓存的文件，
                #   数量天然 ≤ 下载上限，无需再截断；
                # 缓存里更早运行遗留的未评分文件不拉入本次 AI，只有再次被检索命中
                #   进入 to_fetch 时才评分。
                this_run_pubs = {a.get("publication_number", "")
                                 for a in to_fetch if a.get("publication_number")}
                new_pubs = set()
                old_unscored = 0
                for f in sorted(Path(details_dir).glob("*.json")):
                    try:
                        d = json_module.loads(f.read_text(encoding="utf-8"))
                        if d.get("fetch_status") != "ok" or not d.get("claims"):
                            continue
                        pub = d.get("publication_number", "")
                        rec = history.get(pub)
                        if rec and rec.get("best_score", 0) > 0:
                            d["fulltext_score"] = rec["best_score"]
                            d["fulltext_reason"] = rec.get(
                                "best_reason", "历史记录复用")
                            d["key_features"] = rec.get("key_features", [])
                            d["_history_reused"] = True
                            all_scored.append(d)
                        elif pub in this_run_pubs:
                            new_pubs.add(pub)
                        else:
                            old_unscored += 1
                    except Exception:
                        pass
                if all_scored:
                    self.signals.log.emit("INFO",
                        f"  历史记录复用: {len(all_scored)} 篇已评分，跳过 AI")
                if old_unscored:
                    self.signals.log.emit("INFO",
                        f"  缓存遗留未评分 {old_unscored} 篇（非本次下载），本次跳过，"
                        f"再次命中检索时才评分")
                if new_pubs:
                    self.signals.log.emit("INFO",
                        f"  本次有效新增: {len(new_pubs)} 篇 (≤ 下载上限 {max_detail})，"
                        f"开始 Claims 广筛...")
                    new_scored = screener2.screen_claims_all(
                        self.patent_doc, str(details_dir),
                        signals=self.signals,
                        log_dir=str(output_dir / "ai_logs"),
                        only_pubs=new_pubs)
                    all_scored.extend(new_scored)
                all_scored.sort(
                    key=lambda x: x.get("fulltext_score",
                                        x.get("relevance_score", 0)),
                    reverse=True)
            else:
                # 无历史库（未传 cache_dir）→ 全量广筛
                all_scored = screener2.screen_claims_all(
                    self.patent_doc, str(details_dir),
                    signals=self.signals,
                    log_dir=str(output_dir / "ai_logs"))
        else:
            # 无本申请信息，加载全部
            import glob as glob_module
            all_scored = []
            for f in sorted(Path(details_dir).glob("*.json")):
                try:
                    d = json_module.loads(f.read_text(encoding="utf-8"))
                    if d.get("fetch_status") == "ok":
                        d["fulltext_score"] = 50
                        all_scored.append(d)
                except Exception:
                    pass

        self.signals.log.emit("SUCCESS",
            f"阶段4 完成: 共评分 {len(all_scored)} 篇")

        # ── 写回历史记录库（记录 detail_file + 广筛结果）──
        if history is not None and all_scored:
            from src.web_automation.patentscope_scraper import _safe_filename
            for r in all_scored:
                pub = r.get("publication_number", "")
                if pub:
                    r["_detail_file"] = str(
                        Path(details_dir) / f"{_safe_filename(pub)}.json")
            history.merge_screened(all_scored)
            history.save()
            reused = sum(1 for r in all_scored if r.get("_history_reused"))
            self.signals.log.emit("SUCCESS",
                f"  历史记录库已更新: {history.path.name} "
                f"({len(all_scored)} 篇, 其中 {reused} 篇历史复用)")
        for i, r in enumerate(all_scored[:10]):
            score = r.get("fulltext_score", r.get("relevance_score", "?"))
            pub = r.get("publication_number", "?")
            title = str(r.get("title", ""))[:60]
            self.signals.log.emit("INFO",
                f"  [{i+1}] {pub} (相关度: {score}) {title}")
        if len(all_scored) > 10:
            self.signals.log.emit("INFO",
                f"  ... 共 {len(all_scored)} 篇，详细对比阶段将精选处理")

        # 保存评分结果（只存分数+元数据，不含全文）
        light_results = []
        for r in all_scored:
            light_results.append({
                "doc_id": r.get("doc_id", ""),
                "publication_number": r.get("publication_number", ""),
                "title": r.get("title", ""),
                "ipc": r.get("ipc", ""),
                "applicant": r.get("applicant", ""),
                "publication_date": r.get("publication_date", ""),
                "fulltext_score": r.get("fulltext_score", 0),
                "fulltext_reason": r.get("fulltext_reason", ""),
                "key_features": r.get("key_features", []),
            })
        stage4_path = output_dir / "03_ai_screened.json"
        self._save_json(stage4_path, {
            "stage": "ai_fulltext_screened",
            "timestamp": datetime.now().isoformat(),
            "total_downloaded": len(to_fetch),
            "total_scored": len(light_results),
            "max_detail_fetch": max_detail,
            "results": light_results,
        })
        self.signals.log.emit("INFO", f"  已保存: {stage4_path}")

        # 评分快照：存于本次运行的输出目录（自己的文件夹），供综述合并
        if (output_dir and self.patent_doc
                and getattr(self.patent_doc, "publication_number", None)):
            from src.analysis.history import save_score_snapshot
            snap_results = []
            for r in all_scored:
                pub = r.get("publication_number", "")
                snap_results.append({
                    "publication_number": pub,
                    "title": r.get("title", ""),
                    "applicant": r.get("applicant", ""),
                    "ipc": r.get("ipc", ""),
                    "publication_date": r.get("publication_date", ""),
                    "fulltext_score": r.get(
                        "fulltext_score", r.get("relevance_score", 0)),
                    "fulltext_reason": r.get(
                        "fulltext_reason", r.get("relevance_reason", "")),
                    "key_features": r.get("key_features", []),
                    "detail_file": r.get("_detail_file", ""),
                })
            snap_path = save_score_snapshot(
                output_dir, self.patent_doc.publication_number,
                datetime.now().isoformat(timespec="seconds"),
                snap_results,
                source_queries=[q.get("query_string", "")
                                for q in (self.queries or [])],
                content_mode=self.settings.analysis_screen_content)
            self.signals.log.emit("INFO", f"  评分快照已保存: {snap_path}")

        # 传给详细对比阶段的只取 Top N
        detail_n = self.settings.analysis_top_n
        top_for_compare = all_scored[:detail_n]

        # ================================================================
        # 完成
        # ================================================================
        if self._is_running:
            self.signals.log.emit("SUCCESS",
                f"PATENTSCOPE 检索完成: 下载 {len(to_fetch)} 篇全文, "
                f"评分 {len(all_scored)} 篇")
            self.signals.log.emit("INFO",
                f"所有结果已保存到: {output_dir}")

            # ── 断点：评分后 ──
            if self.stop_after == "score":
                self._stop_here("Claims广筛评分", [all_scored])
                return

            self.signals.log.emit("INFO",
                f"传 {len(top_for_compare)} 篇进入详细对比")
            self.signals.progress.emit(55, "检索完成，准备分析...")
            self.signals.all_searches_done.emit([top_for_compare])
            self.signals.finished.emit(True, "")
        else:
            self.signals.finished.emit(True, "用户停止")

    def _stop_here(self, label: str, results: list, saved_path=None):
        """断点停止，发射结果到 UI"""
        total = sum(len(r) for r in results)
        self.signals.log.emit("WARN",
            f"🔧 流程断点: {label} ({total} 篇)")
        if saved_path:
            self.signals.log.emit("INFO", f"  已保存: {saved_path}")
        self.signals.progress.emit(100, f"断点: {label}")
        self.signals.all_searches_done.emit(results)
        self.signals.finished.emit(True, f"断点: {label}")

    @staticmethod
    def _save_json(path, data):
        import json as json_module
        with open(path, "w", encoding="utf-8") as f:
            json_module.dump(data, f, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _write_summary_txt(abstracts: list[dict], output_dir):
        """生成可读检索摘要文本文件"""
        import json as json_module
        from pathlib import Path
        out = Path(output_dir)

        lines = []
        lines.append("=" * 70)
        lines.append("  专利检索摘要报告")
        lines.append("=" * 70)
        lines.append(f"  共 {len(abstracts)} 篇")
        lines.append("")

        for i, a in enumerate(abstracts, 1):
            pub = a.get("publication_number", "?")
            title = a.get("title", "")
            ipc = a.get("ipc", "")
            applicant = a.get("applicant", "")
            snippet = (a.get("abstract_snippet") or "")[:120]
            source = a.get("source_query", "")

            lines.append(f"[{i:3d}] {pub}")
            lines.append(f"       标题: {title[:80]}")
            if ipc:
                lines.append(f"       IPC : {ipc}")
            if applicant:
                lines.append(f"       申请人: {applicant[:60]}")
            if snippet:
                lines.append(f"       摘要: {snippet}...")
            lines.append(f"       来源检索式: {source[:80]}")
            lines.append("-" * 70)

        summary_path = out / "00_检索摘要.txt"
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        # 也保存一份 JSON 方便程序读取
        summary_json = out / "00_检索摘要.json"
        summary_json.write_text(
            json_module.dumps(abstracts, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")

    def _filter_self_patent(self, abstracts: list[dict]) -> tuple[list[dict], int]:
        """过滤掉目标专利自身（公布号匹配）。

        返回: (过滤后列表, 过滤掉的篇数)
        """
        if not self.patent_doc:
            return abstracts, 0

        target_pn = _normalize_pn(
            self.patent_doc.publication_number or ""
        )
        if not target_pn:
            return abstracts, 0

        # 去掉国家前缀的纯数字版本（如 CN116110953 → 116110953）
        target_digits = _strip_country_prefix(target_pn)

        filtered = []
        removed = 0
        for a in abstracts:
            pn = _normalize_pn(a.get("publication_number", ""))
            doc_id = _normalize_pn(a.get("doc_id", ""))
            pn_digits = _strip_country_prefix(pn)
            doc_digits = _strip_country_prefix(doc_id)
            # 公布号或 doc_id 任一匹配即视为自身（仅精确匹配+数字匹配）
            if (pn == target_pn or doc_id == target_pn
                    or pn_digits == target_digits or doc_digits == target_digits):
                removed += 1
            else:
                filtered.append(a)
        return filtered, removed

    # ================================================================
    # 日期淘汰：公开日 >= 本申请截止日（申请日/优先权日）的对比文件直接剔除
    # ================================================================

    def _application_cutoff(self):
        """本申请截止日 date | None。取 min(申请日, 优先权日)，两者都无返回 None。"""
        if not self.patent_doc:
            return None
        from src.utils.date_filter import effective_cutoff_date
        return effective_cutoff_date(
            getattr(self.patent_doc, "application_date", "") or "",
            getattr(self.patent_doc, "priority_date", "") or "")

    def _apply_date_filter(self, patents: list[dict],
                           stage_label: str) -> list[dict]:
        """对候选对比文件执行日期淘汰，返回过滤后的列表。

        公开日 >= 截止日 → 淘汰；公开日缺失/无法解析 → 保留。
        未提供申请日/优先权日 → 不淘汰（提示可手动填写）。
        """
        cutoff = self._application_cutoff()
        if cutoff is None:
            self.signals.log.emit("INFO",
                f"  {stage_label}: 未获取到本申请申请日/优先权日，"
                "跳过日期淘汰（可在界面手动填写申请日）")
            return patents
        from src.utils.date_filter import filter_by_application_date
        kept, eliminated = filter_by_application_date(patents, cutoff)
        if eliminated:
            self.signals.log.emit("WARN",
                f"  {stage_label}: 淘汰 {len(eliminated)} 篇（公开日 ≥ "
                f"截止日 {cutoff}）")
            for e in eliminated[:10]:
                self.signals.log.emit("DEBUG",
                    f"    淘汰: {e.get('publication_number', '?')} "
                    f"(公开日 {e.get('publication_date', '?')})")
            if len(eliminated) > 10:
                self.signals.log.emit("DEBUG",
                    f"    其余 {len(eliminated)-10} 篇略")
        if len(kept) != len(patents):
            self.signals.log.emit("INFO",
                f"  {stage_label}: 日期淘汰后剩余 {len(kept)} 篇")
        return kept

    def _prune_eliminated_details(self, details_dir, cutoff) -> int:
        """阶段3下载后二次校验：删除公开日 >= 截止日的详情缓存文件。

        搜索阶段公开日缺失/抓错的对比文件，这里用详情页的权威公开日兜住。
        返回删除篇数。
        """
        from pathlib import Path
        from src.web_automation.patentscope_scraper import _safe_filename
        from src.utils.date_filter import is_eliminated_by_date

        removed = 0
        for f in sorted(Path(details_dir).glob("*.json")):
            try:
                import json as json_module
                d = json_module.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not is_eliminated_by_date(d, cutoff):
                continue
            pub = d.get("publication_number", f.stem)
            f.unlink(missing_ok=True)
            removed += 1
            self.signals.log.emit("WARN",
                f"  二次校验淘汰: {pub} (公开日 {d.get('publication_date', '?')} "
                f"≥ 截止日 {cutoff})")
        return removed


def _normalize_pn(pn: str) -> str:
    """标准化公布号: 去空格去横杠去斜杠大写"""
    return pn.replace(" ", "").replace("-", "").replace("/", "").upper()


def _strip_country_prefix(pn: str) -> str:
    """去掉国家前缀: CN116110953 → 116110953"""
    import re as _re
    return _re.sub(r'^[A-Z]{2}', '', pn)


def _extract_cited_patent_numbers(text: str) -> list[str]:
    """从说明书文本中提取引用专利号。

    支持格式: CN110000000A, US12345678B2, WO2020000000A1,
             EP12345678A1, JP2020000000A, KR1020200000000A 等
    """
    import re
    if not text:
        return []
    patterns = [
        r'\bCN\s*\d{7,13}[A-Z]?\d*\b',    # 中国
        r'\bUS\s*\d{4,11}[A-Z]?\d*\b',    # 美国
        r'\bWO\s*\d{2,4}[/-]?\d{4,8}[A-Z]?\d*\b',  # PCT
        r'\bEP\s*\d{4,11}[A-Z]?\d*\b',    # 欧洲
        r'\bJP\s*\d{4,11}[A-Z]?\d*\b',    # 日本
        r'\bKR\s*\d{4,11}[A-Z]?\d*\b',    # 韩国
    ]
    seen = set()
    results = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            pn = m.group(0).replace(" ", "").replace("/", "").replace("-", "").upper()
            if pn not in seen:
                seen.add(pn)
                results.append(pn)
    return results


class FinalReviewWorker(QThread):
    """终选评述 Worker — 从历史最佳候选池中挑最终 N 篇做详细评述。

    单独手动触发（UI「终选评述」按钮）：
      1. 加载历史记录库（ScreeningHistory，用于评述缓存复用）
      2. 读全部评分快照文件合并为候选池，LLM 挑最终 final_n 篇
      3. 每篇：有 detailed_review → 复用历史；否则读全文缓存 →
         _detailed_comparison → 写回历史
      4. 渲染 06_终选评述.md，发射 comparisons 供后续 OA
    """

    review_done = Signal(list)       # 终选评述结果 comparisons（含 source_raw 全文）
    review_markdown = Signal(str)    # 渲染后的 markdown

    def __init__(self, patent_doc, settings, pdf_path: str,
                 final_n: int | None = None, top_pool: int | None = None,
                 min_score: int | None = None,
                 ai_provider: str | None = None,
                 snapshot_files: list | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.settings = settings
        self.pdf_path = pdf_path
        self.final_n = (final_n if final_n is not None
                        else settings.analysis_final_review_n)
        self.top_pool = (top_pool if top_pool is not None
                         else settings.analysis_final_pool_top_n)
        self.min_score = (min_score if min_score is not None
                          else settings.analysis_final_pool_min_score)
        self.ai_provider = ai_provider
        # 用户勾选的评分快照文件（None=读全部）
        self.snapshot_files = snapshot_files
        self.signals = WorkerSignals()

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.signals.error.emit(f"终选评述失败: {e}")
            self.signals.log.emit("ERROR", f"终选评述失败: {e}")
            self.signals.finished.emit(False, str(e))

    def _run(self):
        import json as json_module
        from pathlib import Path

        from src.ai_client import AIClient
        from src.analysis.comparator import PatentComparator
        from src.analysis.history import ScreeningHistory
        from src.analysis.screener import PatentScreener
        from src.web_automation.patentscope_scraper import _safe_filename

        if not self.pdf_path:
            self.signals.log.emit("ERROR", "请先选择专利申请 PDF（用于定位历史记录库）")
            self.signals.finished.emit(False, "缺少 PDF 路径")
            return
        pub_num = (self.patent_doc.publication_number or ""
                   if self.patent_doc else "")
        if not pub_num:
            self.signals.log.emit("ERROR", "本申请缺少公布号，无法定位历史记录")
            self.signals.finished.emit(False, "缺少公布号")
            return

        history = ScreeningHistory.from_pdf(self.pdf_path, pub_num)
        # 候选池：读该申请的所有评分快照文件，合并取历史最高分，降序取 top_n
        from src.analysis.history import load_score_snapshots
        pool = load_score_snapshots(
            Path(self.pdf_path).parent, pub_num,
            min_score=self.min_score, top_n=self.top_pool,
            files=self.snapshot_files)
        if not pool:
            self.signals.log.emit("WARN",
                f"没有评分快照或达标记录（best_score ≥ {self.min_score}）。\n"
                f"请先运行「开始分析」完成检索+Claims 广筛。")
            self.signals.finished.emit(True, "无候选")
            return

        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO",
            f"终选评述: 候选池 {len(pool)} 篇 (best_score≥{self.min_score})，"
            f"从中选 {self.final_n} 篇做详细评述")

        # ── 候选池紧凑文本 → LLM 终选 ──
        client = AIClient(self.settings, provider=self.ai_provider)
        screener = PatentScreener(self.settings, provider=self.ai_provider)
        patent_summary = screener._build_patent_summary(self.patent_doc)

        lines = []
        for i, rec in enumerate(pool):
            kf = "、".join(rec.get("key_features") or [])[:200]
            lines.append(
                f"[{i+1}] {rec.get('publication_number','')} | "
                f"评分:{rec.get('best_score','?')} | "
                f"{str(rec.get('title',''))[:60]}\n"
                f"    理由: {str(rec.get('best_reason',''))[:150]}\n"
                f"    关键特征: {kf}")
        pool_text = "\n".join(lines)

        user_prompt = render_template(
            load_prompt(self.settings, "final_review", "user",
                        FINAL_REVIEW_FALLBACK_USER_PROMPT),
            patent_summary=patent_summary, pool_size=len(pool),
            pool_text=pool_text, final_n=self.final_n)

        system_prompt = load_prompt(self.settings, "final_review", "system",
                                    FINAL_REVIEW_FALLBACK_SYSTEM_PROMPT)

        self.signals.progress.emit(5, "LLM 终选候选...")
        self.signals.log.emit("INFO", "  LLM 从候选池终选...")
        try:
            response = client.chat(
                system_prompt=system_prompt, user_prompt=user_prompt,
                max_tokens=4096, temperature=0.3,
                model=self.settings.ai_screen_model)
        except Exception as e:
            self.signals.log.emit("ERROR", f"  LLM 终选失败: {e}")
            self.signals.finished.emit(False, f"终选失败: {e}")
            return

        selected_pubs = screener._parse_pub_list(response)
        pool_pubs = {r.get("publication_number", "") for r in pool}
        selected_pubs = [p for p in selected_pubs if p in pool_pubs]
        if not selected_pubs:
            self.signals.log.emit("WARN",
                "  LLM 未返回有效公布号，退回候选池前几篇")
            selected_pubs = [r.get("publication_number", "")
                             for r in pool[:self.final_n]]

        self.signals.log.emit("SUCCESS",
            f"  终选 {len(selected_pubs)} 篇: {', '.join(selected_pubs)}")

        # ── 逐篇详细评述：复用历史 or 调 _detailed_comparison ──
        from src.utils.paths import patent_detail_dir
        cache_dir = patent_detail_dir(Path(self.pdf_path).parent)
        comparator = PatentComparator(self.settings, provider=self.ai_provider)
        comp_client = comparator._get_client()

        comparisons = []
        total = len(selected_pubs)
        for i, sel_pub in enumerate(selected_pubs):
            rec = next((p for p in pool
                        if p.get("publication_number") == sel_pub), {}) or {}
            # 组装 result（元数据 + 全文，供 _detailed_comparison 使用）
            result = {
                "publication_number": sel_pub,
                "title": rec.get("title", ""),
                "applicant": rec.get("applicant", ""),
                "ipc": rec.get("ipc", ""),
                "publication_date": rec.get("publication_date", ""),
                "claims": "", "description": "", "abstract": "",
            }
            detail_file = cache_dir / f"{_safe_filename(sel_pub)}.json"
            if detail_file.exists():
                try:
                    detail = json_module.loads(
                        detail_file.read_text(encoding="utf-8"))
                    for k in ("claims", "description", "abstract"):
                        if detail.get(k):
                            result[k] = detail[k]
                except Exception:
                    pass

            if history.has_detailed_review(sel_pub):
                self.signals.log.emit("INFO",
                    f"  [{i+1}/{total}] {sel_pub} 复用历史评述")
                review = dict(rec["detailed_review"])
                review.pop("reviewed_at", None)
                # 读完全文的详细评分为准；缺失时才回退 best_score（粗筛评分）
                review["relevance_score"] = (
                    review.get("relevance_score")
                    or rec.get("best_score", 0))
                review["source_raw"] = result
                comparisons.append(review)
            else:
                self.signals.log.emit("INFO",
                    f"  [{i+1}/{total}] {sel_pub} 详细评述...")
                self.signals.progress.emit(
                    15 + int((i + 1) / total * 55), f"评述 {sel_pub}")
                detail = comparator._detailed_comparison(
                    comp_client, self.patent_doc, result)
                if detail:
                    # 读完全文的 AI 评分为准；缺失时才回退 best_score（粗筛评分）
                    detail["relevance_score"] = (
                        detail.get("relevance_score")
                        or rec.get("best_score", 0))
                    comparisons.append(detail)
                    # 只存评述字段，不含全文，保持历史库轻量
                    history.set_detailed_review(sel_pub, {
                        k: detail.get(k) for k in (
                            "publication_number", "relevance_score",
                            "novelty_impact", "inventive_step_impact",
                            "key_features_same", "key_features_different",
                            "conclusion")})
                else:
                    self.signals.log.emit("WARN",
                        f"    {sel_pub} 详细评述失败")

        history.save()
        self.signals.log.emit("SUCCESS",
            f"终选评述完成: {len(comparisons)} 篇，历史记录库已更新")

        # ── 渲染 markdown 报告并落盘 ──
        md = self._render_markdown(comparisons)
        out_md = Path(self.pdf_path).parent / "06_终选评述.md"
        try:
            out_md.write_text(md, encoding="utf-8")
            self.signals.log.emit("INFO", f"  已保存: {out_md}")
        except Exception as e:
            self.signals.log.emit("WARN", f"  报告保存失败: {e}")

        self.review_markdown.emit(md)
        self.review_done.emit(comparisons)
        self.signals.progress.emit(100, "终选评述完成")
        self.signals.finished.emit(True, f"终选评述 {len(comparisons)} 篇")

    def _render_markdown(self, comparisons: list[dict]) -> str:
        """把终选评述渲染成 Markdown（供报告面板 + 落盘）"""
        lines = ["# 终选评述（从历史最佳对比文件中筛选）", ""]
        if self.patent_doc:
            lines.append(f"**本申请**: {self.patent_doc.title or '?'}")
            if self.patent_doc.publication_number:
                lines.append(
                    f"**公布号**: {self.patent_doc.publication_number}")
        lines.append("")

        def impact_label(v):
            m = {"high": "⚠️ 高（可能影响授权）",
                 "moderate": "⚡ 中（需要关注）",
                 "low": "✅ 低（影响有限）"}
            return m.get(str(v).lower(), str(v))

        for i, c in enumerate(comparisons, 1):
            sel_pub = c.get("publication_number", "?")
            src = c.get("source_raw", {}) or {}
            title = c.get("title", "") or src.get("title", "")
            score = c.get("relevance_score", "?")
            novelty = c.get("novelty_impact", "?")
            inventive = c.get("inventive_step_impact", "?")
            same = c.get("key_features_same", []) or []
            diff = c.get("key_features_different", []) or []
            conclusion = c.get("conclusion", "")
            lines.append(f"## {i}. {sel_pub} (相关度 {score})")
            lines.append("")
            if title:
                lines.append(f"- **标题**: {title}")
            lines.append(f"- **新颖性影响**: {impact_label(novelty)}")
            lines.append(f"- **创造性影响**: {impact_label(inventive)}")
            lines.append("")
            lines.append("### 相同技术特征")
            lines.append("\n".join(f"- {f}" for f in same)
                         if same else "- *(AI 未列出)*")
            lines.append("")
            lines.append("### 不同技术特征")
            lines.append("\n".join(f"- {f}" for f in diff)
                         if diff else "- *(AI 未列出)*")
            lines.append("")
            if conclusion:
                lines.append("### 综合结论")
                lines.append(conclusion)
            lines.append("")
        return "\n".join(lines)


class AnalysisWorker(QThread):
    """对比分析后台线程"""

    def __init__(self, patent_doc, dedup_results: list, settings: Settings,
                 ai_provider: str | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.dedup_results = dedup_results
        self.settings = settings
        self.ai_provider = ai_provider
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.progress.emit(85, "正在进行对比分析...")
            self.signals.log.emit("INFO", f"开始对比分析: {len(self.dedup_results)} 篇对比文献")

            from src.analysis.comparator import PatentComparator
            from src.analysis.report import AnalysisReport

            comparator = PatentComparator(self.settings, provider=self.ai_provider)
            comparisons = comparator.compare_batch(
                self.patent_doc, self.dedup_results
            )

            report = AnalysisReport(
                patent_doc=self.patent_doc,
                comparisons=comparisons,
                dedup_results=self.dedup_results,
            )
            report.generate()

            self.signals.log.emit("SUCCESS", "对比分析完成")
            self.signals.progress.emit(100, "分析完成")
            self.signals.analysis_done.emit(report)
            self.signals.finished.emit(True, "")
        except Exception as e:
            self.signals.error.emit(f"对比分析失败: {e}")
            self.signals.log.emit("ERROR", f"对比分析失败: {e}")
            self.signals.finished.emit(False, str(e))


class OAWriterWorker(QThread):
    """审查意见通知书撰写后台线程"""

    def __init__(self, patent_doc, dedup_results: list,
                 comparisons: list, settings: Settings,
                 ai_provider: str | None = None, options: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.dedup_results = dedup_results
        self.comparisons = comparisons
        self.settings = settings
        self.ai_provider = ai_provider
        self.options = options or {}
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.progress.emit(90, "正在撰写审查意见通知书...")
            self.signals.log.emit("INFO", "AI 撰写审查意见通知书中...")

            from src.analysis.oa_writer import OAWriter

            writer = OAWriter(self.settings, provider=self.ai_provider)
            oa_markdown = writer.write(
                self.patent_doc, self.comparisons, self.dedup_results,
                options=self.options)

            self.signals.log.emit("SUCCESS", "审查意见通知书撰写完成")
            self.signals.progress.emit(100, "全部完成")
            self.signals.analysis_done.emit(oa_markdown)
            self.signals.finished.emit(True, "")
        except Exception as e:
            self.signals.error.emit(f"通知书撰写失败: {e}")
            self.signals.log.emit("ERROR", f"通知书撰写失败: {e}")
            self.signals.finished.emit(False, str(e))


class PatentLookupWorker(QThread):
    """公布号直查 Worker：每次查询用完即关浏览器，下次再开新的，永不被限流。"""

    lookup_done = Signal(dict)

    def __init__(self, query: str, settings: Settings, parent=None):
        super().__init__(parent)
        self.query = query.strip()
        self.settings = settings
        self.signals = WorkerSignals()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._run_async())
            loop.close()
            if result:
                self.lookup_done.emit(result)
                self.signals.finished.emit(True, "")
            else:
                self.signals.error.emit(f"未找到专利: {self.query}")
                self.signals.finished.emit(False, "未找到")
        except Exception as e:
            self.signals.error.emit(f"查询失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self) -> dict | None:
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.patentscope_scraper import PatentscopeScraper
        from src.web_automation.human_behavior import HumanBehavior

        # 清洗查询词
        import re as _re
        q = self.query.strip()
        q = _re.sub(r'\s+', '', q)
        self.signals.log.emit("INFO", f"查询: {q}")

        # ── Google 引擎：免浏览器，纯 HTTP 从 Google Patents 获取本申请全文 ──
        if self.settings.search_source == "google":
            from src.web_automation.google_patents import fetch_patent_text
            import asyncio as _asyncio
            for attempt in range(1, 3):
                try:
                    result = fetch_patent_text(
                        q, proxy=self.settings.web_proxy,
                        timeout=self.settings.google_patents_timeout)
                    if result and result.get("fetch_status") == "ok":
                        self.signals.log.emit("SUCCESS",
                            f"查询完成: claims={len(result.get('claims',''))} "
                            f"desc={len(result.get('description',''))} (Google Patents)")
                        return result
                except Exception as e:
                    self.signals.log.emit("WARN",
                        f"  Google 查询失败(第{attempt}次): {e}")
                if attempt < 2:
                    await _asyncio.sleep(2)
            self.signals.log.emit("ERROR", f"Google Patents 查询失败: {q}")
            return None

        # ── WIPO 引擎（原行为）：搜索需要去掉类别码 ──
        q = _re.sub(r'[ABU]\d?$', '', q)

        MAX_RETRIES = 3
        for attempt in range(1, MAX_RETRIES + 1):
            mgr = BrowserManager(self.settings)
            try:
                context, page = await mgr.launch_with_retry(max_retries=1)
                human = HumanBehavior(page)
                scraper = PatentscopeScraper(page, self.settings, human)

                result = await scraper.fetch_detail_via_search(q, signals=self.signals)

                if result:
                    self.signals.log.emit("SUCCESS",
                        f"查询完成: claims={len(result.get('claims',''))} "
                        f"desc={len(result.get('description',''))}")
                    return result
                else:
                    self.signals.log.emit("WARN", f"未找到: {q}")
                    return None
            except Exception as e:
                err_msg = str(e)
                is_403 = "403" in err_msg and ("Forbidden" in err_msg or "FORBIDDEN" in err_msg)
                is_net_error = any(kw in err_msg for kw in (
                    "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                    "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                    "NS_BINDING_ABORTED", "net::ERR_",
                    "NS_ERROR_PROXY_CONNECTION_REFUSED",
                    "NS_ERROR_UNKNOWN_HOST", "NS_ERROR_UNKNOWN_PROXY_HOST",
                ))
                if (is_403 or is_net_error) and attempt < MAX_RETRIES:
                    if is_403:
                        new_browser = BrowserManager.switch_channel(on_403=True)
                        cool = random.uniform(8, 20)
                        reason = "403"
                    else:
                        new_browser = BrowserManager.switch_channel()
                        cool = random.uniform(3, 8)
                        reason = "网络中断"
                    self.signals.log.emit("WARN",
                        f"  查询遇到 {reason}，切换至 {new_browser} + 冷却 {cool:.0f}s "
                        f"(第 {attempt}/{MAX_RETRIES} 次重试)...")
                    await asyncio.sleep(cool)
                else:
                    self.signals.log.emit("ERROR", f"查询失败: {e}")
                    return None
            finally:
                await BrowserManager.shutdown()

        return None


class MultiQueryTestWorker(QThread):
    """批量检索式测试 Worker — 搜索 → 去重 → 下载 → 报告。

    纯数据验证管线，零 AI 依赖。
    """

    def __init__(self, queries: list[str], settings: "Settings",
                 test_name: str = "", max_results: int = 100,
                 concurrency: int = 1,
                 output_dir: str | None = None, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.test_name = test_name
        self.max_results = max_results
        self.concurrency = concurrency
        self._given_output_dir = output_dir
        self.signals = WorkerSignals()
        self._is_running = True
        self.output_dir = None

    def stop(self):
        self._is_running = False
        from src.web_automation.browser_manager import BrowserManager
        BrowserManager.cancel_cooldown()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"批量测试失败: {e}")
            self.signals.log.emit("ERROR", f"批量测试失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        import re as _re
        import json as json_module
        from datetime import datetime
        from pathlib import Path
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.human_behavior import HumanBehavior
        from src.web_automation.patentscope_scraper import (
            PatentscopeScraper, is_cached_patent_valid, _safe_filename)

        # ── 输出目录 ──────────────────────────────────────────────
        if self._given_output_dir:
            self.output_dir = Path(self._given_output_dir)
        else:
            name = _re.sub(r'[\\/:*?"<>|]', '_', self.test_name or "batch_test")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.output_dir = Path.cwd() / "data" / "output" / "test_multi" / f"{name}_{ts}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        out = self.output_dir
        detail_dir = out / "02_patent_details"
        detail_dir.mkdir(parents=True, exist_ok=True)

        # 保存输入检索式
        self._save_json(out / "queries.json", {
            "test_name": self.test_name,
            "max_results": self.max_results,
            "concurrency": self.concurrency,
            "queries": self.queries,
        })

        # ============================================================
        # 阶段1: 逐条搜索摘要
        # ============================================================
        self.signals.log.emit("INFO", "=" * 50)
        eng_name = "Google Patents" if self.settings.search_source == "google" else "PATENTSCOPE (WIPO)"
        self.signals.log.emit("INFO",
            f"批量检索测试: {len(self.queries)} 个检索式 × {self.max_results} 条/式")
        self.signals.log.emit("SUCCESS",
            f"检索引擎: {eng_name}")
        self.signals.progress.emit(5, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)
        self.signals.log.emit("SUCCESS", "浏览器就绪")

        human = HumanBehavior(self.settings)
        scraper = PatentscopeScraper(page, self.settings, human)

        all_abstracts = []
        per_query_stats = []
        per_query_dir = out / "per_query"
        per_query_dir.mkdir(parents=True, exist_ok=True)

        MAX_SEARCH_RETRIES = 3

        if self.settings.search_source == "google" and self.queries:
            # ══ Google：并行搜索全部检索式（多标签页）══
            from src.web_automation.google_patents import (
                search_abstracts_parallel as gsearch_parallel)
            q_strings = [str(q).strip() for q in self.queries]
            self.signals.log.emit("INFO",
                f"阶段1: 并行搜索 {len(self.queries)} 个检索式 "
                f"(并发 {self.settings.search_search_concurrency})...")
            self.signals.progress.emit(5, "阶段1: 并行搜索...")

            per_query = await gsearch_parallel(
                page, q_strings, max_results=self.max_results,
                signals=self.signals,
                concurrency=self.settings.search_search_concurrency)

            # ── 失败的检索式：切浏览器 + 冷却 + 重试（最多3轮）──
            failed_idx = [i for i, r in enumerate(per_query) if r.get("error")]
            retry_round = 0
            while failed_idx and retry_round < MAX_SEARCH_RETRIES and self._is_running:
                retry_round += 1
                err_text = "\n".join(per_query[i]["error"] or "" for i in failed_idx)
                is_403 = "403" in err_text
                is_net_error = any(kw in err_text for kw in (
                    "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                    "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                    "NS_BINDING_ABORTED", "net::ERR_",
                ))
                if not (is_403 or is_net_error):
                    break  # 非限流错误，重试无意义
                if is_403:
                    BrowserManager.switch_channel(on_403=True)
                    cool = random.uniform(8, 20)
                    reason = "403"
                else:
                    BrowserManager.switch_channel()
                    cool = random.uniform(3, 8)
                    reason = "网络中断"
                self.signals.log.emit("WARN",
                    f"  并行搜索 {len(failed_idx)} 个检索式遇 {reason}，"
                    f"冷却 {cool:.0f}s 重试 ({retry_round}/{MAX_SEARCH_RETRIES})...")
                await browser_mgr.close()
                await asyncio.sleep(cool)
                try:
                    context, page = await browser_mgr.launch_with_retry(max_retries=1)
                except Exception:
                    await asyncio.sleep(2)
                    context, page = await browser_mgr.launch_with_retry(max_retries=1)
                human = HumanBehavior(self.settings)
                scraper = PatentscopeScraper(page, self.settings, human)
                retry_res = await gsearch_parallel(
                    page, [q_strings[i] for i in failed_idx],
                    max_results=self.max_results, signals=self.signals,
                    concurrency=min(self.settings.search_search_concurrency,
                                    len(failed_idx)))
                for k, i in enumerate(failed_idx):
                    per_query[i] = retry_res[k]
                failed_idx = [i for i in failed_idx if per_query[i].get("error")]

            # ── 收尾：逐式存盘 + 日志 ──
            for q_idx, q_str in enumerate(self.queries):
                if not self._is_running:
                    break
                q_str = q_str.strip()
                if not q_str:
                    continue
                res = per_query[q_idx]
                abstracts = res.get("abstracts", [])
                error_msg = res.get("error")
                for a in abstracts:
                    a["source_query"] = q_str
                all_abstracts.append(abstracts)
                self._save_json(
                    per_query_dir / f"{q_idx + 1:02d}_abstracts.json",
                    {"query": q_str, "count": len(abstracts),
                     "error": error_msg, "results": abstracts})
                per_query_stats.append({
                    "index": q_idx + 1,
                    "query": q_str,
                    "results_count": len(abstracts),
                    "error": error_msg,
                })
                if error_msg:
                    self.signals.log.emit("ERROR",
                        f"  检索式{q_idx + 1} 失败: {error_msg}")
                else:
                    self.signals.log.emit("SUCCESS",
                        f"  检索式{q_idx + 1}: {len(abstracts)} 篇摘要")
            self.signals.progress.emit(25, "阶段1: 搜索完成")
        else:
            # ══ WIPO / 单检索式：串行（原逻辑）══
            for q_idx, q_str in enumerate(self.queries):
                if not self._is_running:
                    break
                q_str = q_str.strip()
                if not q_str:
                    continue

                label = f"检索式{q_idx + 1}"
                self.signals.log.emit("INFO",
                    f"\n--- {label} / {len(self.queries)} ---")
                self.signals.log.emit("INFO", f"  {q_str}")
                pct = 5 + int((q_idx + 1) / max(len(self.queries), 1) * 20)
                self.signals.progress.emit(pct,
                    f"搜索 {q_idx + 1}/{len(self.queries)}: {q_str[:50]}...")

                abstracts = []
                error_msg = None
                for attempt in range(1, MAX_SEARCH_RETRIES + 1):
                    try:
                        abstracts = await scraper.search_abstracts(
                            q_str, max_results=self.max_results, signals=self.signals)
                        break
                    except Exception as e:
                        err_msg = str(e)
                        is_403 = "403" in err_msg
                        is_net_error = any(kw in err_msg for kw in (
                            "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                            "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                            "NS_BINDING_ABORTED", "net::ERR_",
                        ))
                        if (is_403 or is_net_error) and attempt < MAX_SEARCH_RETRIES and self._is_running:
                            if is_403:
                                BrowserManager.switch_channel(on_403=True)
                                cool = random.uniform(8, 20)
                            else:
                                BrowserManager.switch_channel()
                                cool = random.uniform(3, 8)
                            self.signals.log.emit("WARN",
                                f"  搜索遇到{'403' if is_403 else '网络中断'}，"
                                f"冷却 {cool:.0f}s 重试 ({attempt}/{MAX_SEARCH_RETRIES})...")
                            await browser_mgr.close()
                            await asyncio.sleep(cool)
                            try:
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            except Exception:
                                await asyncio.sleep(2)
                                context, page = await browser_mgr.launch_with_retry(max_retries=1)
                            human = HumanBehavior(self.settings)
                            scraper = PatentscopeScraper(page, self.settings, human)
                        else:
                            error_msg = str(e)
                            break

                for a in abstracts:
                    a["source_query"] = q_str
                all_abstracts.append(abstracts)

                self._save_json(
                    per_query_dir / f"{q_idx + 1:02d}_abstracts.json",
                    {"query": q_str, "count": len(abstracts),
                     "error": error_msg, "results": abstracts})

                per_query_stats.append({
                    "index": q_idx + 1,
                    "query": q_str,
                    "results_count": len(abstracts),
                    "error": error_msg,
                })

                if error_msg:
                    self.signals.log.emit("ERROR", f"  {label} 失败: {error_msg}")
                else:
                    self.signals.log.emit("SUCCESS",
                        f"  {label}: {len(abstracts)} 篇摘要")

                if q_idx < len(self.queries) - 1 and self._is_running:
                    await human.inter_search_delay(q_idx + 1)

        await browser_mgr.close()

        # ============================================================
        # 轮询合并去重
        # ============================================================
        self.signals.log.emit("INFO", "\n" + "=" * 50)
        self.signals.log.emit("INFO", "去重合并...")
        seen = set()
        unique_abstracts = []
        max_batch = max((len(b) for b in all_abstracts), default=0)
        for i in range(max_batch):
            for batch in all_abstracts:
                if i < len(batch):
                    a = batch[i]
                    key = a.get("publication_number") or a.get("doc_id", "")
                    if key and key not in seen:
                        seen.add(key)
                        unique_abstracts.append(a)

        total_before = sum(len(b) for b in all_abstracts)
        total_unique = len(unique_abstracts)
        self.signals.log.emit("SUCCESS",
            f"去重: {total_before} 篇 → {total_unique} 篇唯一专利")

        self._save_json(out / "01_all_abstracts.json", {
            "stage": "merged_abstracts",
            "timestamp": datetime.now().isoformat(),
            "total_before_dedup": total_before,
            "total_unique": total_unique,
            "per_query": per_query_stats,
            "results": unique_abstracts,
        })

        if total_unique == 0:
            self.signals.log.emit("WARN", "所有检索式均无结果，停止")
            self._write_report(out, per_query_stats, 0, 0, 0, 0, 0, [])
            self.signals.progress.emit(100, "无结果")
            self.signals.finished.emit(True, "无结果")
            return

        # ============================================================
        # 阶段2: 并行下载完整详情
        # ============================================================
        self.signals.log.emit("INFO", "\n" + "=" * 50)
        self.signals.log.emit("INFO",
            f"下载 {total_unique} 篇完整详情 (并发 {self.concurrency})...")
        self.signals.progress.emit(28, "启动浏览器下载...")

        browser_mgr2 = BrowserManager(self.settings)
        context2, page2 = await browser_mgr2.launch_with_retry(max_retries=2)
        human2 = HumanBehavior(self.settings)
        scraper2 = PatentscopeScraper(page2, self.settings, human2)

        await scraper2.fetch_details_parallel(
            unique_abstracts, str(detail_dir),
            concurrency=self.concurrency, signals=self.signals)

        await browser_mgr2.close()

        # ============================================================
        # 数据质量验证 & 报告
        # ============================================================
        self.signals.log.emit("INFO", "\n" + "=" * 50)
        self.signals.log.emit("INFO", "数据质量分析...")
        self.signals.progress.emit(85, "生成报告...")

        detail_files = sorted(detail_dir.glob("*.json"))
        succeeded = 0
        cached = 0
        failed = 0
        failed_list = []
        quality = {"with_claims": 0, "with_description": 0,
                   "with_abstract": 0, "with_ipc": 0,
                   "total_claims_chars": 0, "total_desc_chars": 0}
        succeeded_patents = []

        for fpath in detail_files:
            try:
                data = json_module.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                failed += 1
                failed_list.append({"file": fpath.name, "error": "JSON解析失败"})
                continue

            status = data.get("fetch_status", "")
            if status == "ok":
                if is_cached_patent_valid(data):
                    succeeded += 1
                    pub = data.get("publication_number", fpath.stem)
                    if data.get("claims"):
                        quality["with_claims"] += 1
                        quality["total_claims_chars"] += len(data["claims"])
                    if data.get("description"):
                        quality["with_description"] += 1
                        quality["total_desc_chars"] += len(data["description"])
                    if data.get("abstract"):
                        quality["with_abstract"] += 1
                    if data.get("ipc"):
                        quality["with_ipc"] += 1
                    succeeded_patents.append({
                        "doc_id": data.get("doc_id", ""),
                        "publication_number": pub,
                        "title": str(data.get("title", ""))[:80],
                        "has_claims": bool(data.get("claims")),
                        "has_description": bool(data.get("description")),
                        "claims_chars": len(data.get("claims") or ""),
                        "desc_chars": len(data.get("description") or ""),
                    })
                else:
                    failed += 1
                    failed_list.append({
                        "file": fpath.name,
                        "doc_id": data.get("doc_id", ""),
                        "error": "缓存内容无效（缺权利要求/说明书）",
                    })
            elif status == "failed":
                failed += 1
                failed_list.append({
                    "file": fpath.name,
                    "doc_id": data.get("doc_id", ""),
                    "error": data.get("error", "未知错误"),
                })
            else:
                if is_cached_patent_valid(data):
                    cached += 1
                else:
                    failed += 1
                    failed_list.append({"file": fpath.name, "error": "缓存无效"})

        total_downloaded = succeeded + cached + failed

        self.signals.log.emit("SUCCESS",
            f"下载统计: 成功 {succeeded}, 缓存 {cached}, 失败 {failed}")
        avg_claims = quality["total_claims_chars"] // max(succeeded, 1)
        avg_desc = quality["total_desc_chars"] // max(succeeded, 1)
        self.signals.log.emit("INFO",
            f"数据质量: 有权利要求 {quality['with_claims']}/{succeeded}, "
            f"有说明书 {quality['with_description']}/{succeeded}, "
            f"有摘要 {quality['with_abstract']}/{succeeded}, "
            f"有IPC {quality['with_ipc']}/{succeeded}")
        self.signals.log.emit("INFO",
            f"  平均权利要求: {avg_claims} 字, 平均说明书: {avg_desc} 字")

        self._write_report(out, per_query_stats, total_before, total_unique,
                          succeeded, cached, failed, failed_list, quality,
                          avg_claims, avg_desc, succeeded_patents)

        self.signals.log.emit("SUCCESS", f"\n{'=' * 50}")
        self.signals.log.emit("SUCCESS", "批量测试完成!")
        self.signals.log.emit("INFO", f"输出目录: {out}")

        self.signals.progress.emit(100, "批量测试完成")

        # 加载完整专利数据发射给结果面板
        full_patents = []
        for fpath in sorted(detail_dir.glob("*.json")):
            try:
                d = json_module.loads(fpath.read_text(encoding="utf-8"))
                if d.get("fetch_status") == "ok" and is_cached_patent_valid(d):
                    full_patents.append(d)
            except Exception:
                pass
        self.signals.all_searches_done.emit([full_patents])
        self.signals.finished.emit(True, f"完成: {succeeded} 篇下载成功")

    # ── 辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _save_json(path, data):
        import json as json_module
        with open(path, "w", encoding="utf-8") as f:
            json_module.dump(data, f, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def _write_report(out_dir, per_query_stats, total_before, total_unique,
                      succeeded, cached, failed, failed_list,
                      quality=None, avg_claims=0, avg_desc=0,
                      succeeded_patents=None):
        import json as json_module
        from datetime import datetime
        from pathlib import Path
        out = Path(out_dir)

        report = {
            "timestamp": datetime.now().isoformat(),
            "per_query": per_query_stats,
            "total_before_dedup": total_before,
            "total_unique": total_unique,
            "download_stats": {"succeeded": succeeded, "cached": cached,
                               "failed": failed},
            "data_quality": {
                "with_claims": quality["with_claims"] if quality else 0,
                "with_description": quality["with_description"] if quality else 0,
                "with_abstract": quality["with_abstract"] if quality else 0,
                "with_ipc": quality["with_ipc"] if quality else 0,
                "average_claims_length": avg_claims,
                "average_description_length": avg_desc,
            } if quality else {},
            "failed_patents": failed_list,
        }
        (out / "report.json").write_text(
            json_module.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")

        lines = []
        lines.append("=" * 70)
        lines.append("  批量检索式测试报告")
        lines.append("=" * 70)
        lines.append(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  检索式数: {len(per_query_stats)}")
        lines.append("")
        lines.append("-" * 70)
        lines.append("  各检索式结果")
        lines.append("-" * 70)
        for s in per_query_stats:
            status = "✓" if not s.get("error") else "✗"
            lines.append(
                f"  [{s['index']:2d}] {status} "
                f"{s.get('results_count', 0):4d} 篇  "
                f"{s.get('query', '')[:70]}")
            if s.get("error"):
                lines.append(f"       错误: {s['error'][:120]}")
        lines.append("")
        lines.append("-" * 70)
        lines.append("  总体统计")
        lines.append("-" * 70)
        lines.append(f"  各检索式合计: {total_before} 篇")
        lines.append(f"  去重后唯一:   {total_unique} 篇")
        if total_before > 0:
            lines.append(
                f"  去重率:       "
                f"{((1 - total_unique / total_before) * 100):.1f}%")
        lines.append("")
        lines.append(f"  下载成功:     {succeeded} 篇")
        lines.append(f"  缓存命中:     {cached} 篇")
        lines.append(f"  下载失败:     {failed} 篇")
        if total_unique > 0:
            lines.append(
                f"  成功率:       "
                f"{(succeeded + cached) / total_unique * 100:.1f}%")
        lines.append("")
        if quality and succeeded > 0:
            lines.append("-" * 70)
            lines.append("  数据质量")
            lines.append("-" * 70)
            lines.append(f"  有权利要求:   {quality['with_claims']}/{succeeded}")
            lines.append(f"  有说明书:     {quality['with_description']}/{succeeded}")
            lines.append(f"  有摘要:       {quality['with_abstract']}/{succeeded}")
            lines.append(f"  有IPC分类:    {quality['with_ipc']}/{succeeded}")
            lines.append(f"  权利要求均长: {avg_claims} 字")
            lines.append(f"  说明书均长:   {avg_desc} 字")
            lines.append("")
        if succeeded_patents:
            lines.append("-" * 70)
            lines.append(f"  成功下载的专利 ({len(succeeded_patents)} 篇)")
            lines.append("-" * 70)
            for i, p in enumerate(succeeded_patents, 1):
                pub = p.get("publication_number", "?")
                title = p.get("title", "")[:60]
                c_len = p.get("claims_chars", 0)
                d_len = p.get("desc_chars", 0)
                lines.append(
                    f"  [{i:3d}] {pub:20s} | "
                    f"权利要求:{c_len:5d}字 | 说明书:{d_len:6d}字 | {title}")
            lines.append("")
        if failed_list:
            lines.append("-" * 70)
            lines.append(f"  下载失败 ({len(failed_list)} 篇)")
            lines.append("-" * 70)
            for f_item in failed_list:
                lines.append(
                    f"  ✗ {f_item.get('file', '?')}: "
                    f"{f_item.get('error', '?')[:100]}")
            lines.append("")
        lines.append("=" * 70)
        lines.append(f"  报告结束 — 输出目录: {out}")
        lines.append("=" * 70)

        (out / "report.txt").write_text(
            "\n".join(lines), encoding="utf-8")
