"""
后台工作线程 - 所有耗时操作在 QThread 中执行
"""
import asyncio

from PySide6.QtCore import QThread, Signal

from src.utils.signals import WorkerSignals
from src.utils.config import Settings


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
      阶段2: 超过上限时 AI 快速粗筛（一批搞定，只选不评）
      阶段3: 并行下载完整详情 → 02_patent_details/
      阶段4: AI 全文精选（全部评分排序）
    """

    def __init__(self, queries: list, settings: Settings,
                 patent_doc=None, max_fetch: int = 200,
                 top_n: int = 25, output_dir=None,
                 cache_dir: str | None = None,
                 debug_search_only: bool = False,
                 include_citations: bool = True, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.patent_doc = patent_doc
        self.max_fetch = max_fetch          # 全文下载上限
        self._given_output_dir = output_dir
        self._cache_dir = cache_dir         # 共享专利缓存目录
        self.debug_search_only = debug_search_only
        self.include_citations = include_citations
        self.signals = WorkerSignals()
        self._is_running = True
        self.output_dir = None

    def stop(self):
        self._is_running = False

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
        self.signals.log.emit("INFO", "阶段1: PATENTSCOPE 搜索摘要")
        self.signals.progress.emit(5, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)
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

            abstracts = await scraper.search_abstracts(
                q_str, max_results=self.settings.patentscope_max_results,
                signals=self.signals)
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
                    key = a.get("doc_id") or a.get("publication_number", "")
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
                abstracts = await scraper.search_abstracts(
                    fq_str, max_results=self.settings.patentscope_max_results,
                    signals=self.signals)
                for a in abstracts:
                    a["source_query"] = fq_str
                fallback_abstracts.append(abstracts)
                self.signals.log.emit("INFO",
                    f"  兜底: {fq_str[:60]}... → {len(abstracts)} 篇")
            await browser_mgr.close()
            seen = set()
            unique_abstracts = []
            for batch in fallback_abstracts:
                for a in batch:
                    key = a.get("doc_id") or a.get("publication_number", "")
                    if key and key not in seen:
                        seen.add(key)
                        unique_abstracts.append(a)
            unique_abstracts, _ = self._filter_self_patent(unique_abstracts)
            total_abstracts = len(unique_abstracts)
            self.signals.log.emit("INFO",
                f"  兜底结果: {total_abstracts} 篇摘要")

        if total_abstracts == 0:
            self.signals.log.emit("WARN", "所有检索式（含宽泛兜底）均未找到结果，停止检索")
            self.signals.finished.emit(True, "无结果")
            return

        self.signals.query_complete.emit(1, 1, unique_abstracts)

        # ── 调试断点：仅搜索模式 ──────────────────────────────────────
        if self.debug_search_only:
            self.signals.log.emit("WARN",
                "🔧 仅搜索模式：阶段1完成，停止（不下载全文）")
            self.signals.log.emit("SUCCESS",
                f"共 {total_abstracts} 篇摘要，已保存到 {stage1_path}")
            # 生成可读摘要文件
            self._write_summary_txt(unique_abstracts, output_dir)
            self.signals.progress.emit(100, "检索摘要完成")
            # 发射实际结果，让 UI 显示
            self.signals.all_searches_done.emit([unique_abstracts])
            self.signals.finished.emit(True, "检索摘要完成")
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
                                    "source_query": f"说明书引用: {pub}",
                                }
                                unique_abstracts.append(item)
                                added += 1
                                continue
                        except Exception:
                            pass

                    # 缓存未命中 → 联网获取
                    self.signals.log.emit("INFO",
                        f"  下载引用专利 [{i+1}/{len(new_pubs)}]: {pub}")
                    try:
                        detail = await scraper1.fetch_detail(pub)
                        if detail:
                            # 保存到缓存
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
                        else:
                            self.signals.log.emit("WARN", f"    未能获取: {pub}")
                    except Exception as e:
                        self.signals.log.emit("WARN", f"    下载失败: {pub} - {e}")
                    await asyncio.sleep(1.0)  # 串行，避免限流

                await browser_mgr1.close()
                self.signals.log.emit("SUCCESS",
                    f"引用专利下载完成: 新增 {added} 篇, 累计 {len(unique_abstracts)} 篇")
                total_abstracts = len(unique_abstracts)

        # ================================================================
        # 阶段2: 超过上限时 AI 快速粗筛（一批搞定，只返回公布号列表）
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        if total_abstracts > max_detail:
            self.signals.log.emit("INFO",
                f"阶段2: 结果数 {total_abstracts} > 下载上限 {max_detail}，"
                f"AI 快速粗筛...")
            self.signals.progress.emit(25, "阶段2: AI 快速粗筛（~30秒）...")

            screener = PatentScreener(self.settings)
            screener._get_client().set_log_dir(
                str(output_dir / "ai_logs"))
            to_fetch = screener.quick_screen(
                self.patent_doc, unique_abstracts,
                top_n=max_detail, signals=self.signals)

            self.signals.log.emit("SUCCESS",
                f"阶段2 完成: {total_abstracts} → {len(to_fetch)} 篇")
        else:
            self.signals.log.emit("INFO",
                f"阶段2: 结果数 {total_abstracts} ≤ 上限 {max_detail}，全部下载全文")
            to_fetch = unique_abstracts

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

        await scraper2.fetch_details_parallel(
            to_fetch, str(details_dir), concurrency=1, signals=self.signals)

        await browser_mgr2.close()

        # ================================================================
        # 阶段4: AI 全文精选（全部评分排序）
        # ================================================================
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO", "阶段4: AI 全文评分...")
        self.signals.progress.emit(45, "阶段4: AI 全文评分...")

        if self.patent_doc:
            screener2 = PatentScreener(self.settings)
            screener2._get_client().set_log_dir(
                str(output_dir / "ai_logs"))
            all_scored = screener2.screen_fulltext(
                self.patent_doc, str(details_dir),
                batch_size=self.settings.analysis_fulltext_batch_size,
                signals=self.signals)
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

        # 传给详细对比阶段的只取 Top N
        detail_n = self.settings.analysis_top_n
        top_for_compare = all_scored[:detail_n]

        # ================================================================
        # 完成
        # ================================================================
        if self._is_running:
            self.signals.log.emit("SUCCESS",
                f"PATENTSCOPE 检索完成: 下载 {len(to_fetch)} 篇全文, "
                f"评分 {len(all_scored)} 篇, 传 {len(top_for_compare)} 篇进入详细对比")
            self.signals.log.emit("INFO",
                f"所有结果已保存到: {output_dir}")
            self.signals.progress.emit(55, "检索完成，准备分析...")
            self.signals.all_searches_done.emit([top_for_compare])
            self.signals.finished.emit(True, "")
        else:
            self.signals.finished.emit(True, "用户停止")

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
            # 公布号或 doc_id 任一匹配即视为自身（匹配含/不含CN前缀）
            if (pn == target_pn or doc_id == target_pn
                    or pn_digits == target_digits or doc_digits == target_digits
                    or pn in target_pn or target_pn in pn
                    or pn_digits in target_pn or target_pn in pn_digits):
                removed += 1
            else:
                filtered.append(a)
        return filtered, removed


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
                 ai_provider: str | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.dedup_results = dedup_results
        self.comparisons = comparisons
        self.settings = settings
        self.ai_provider = ai_provider
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.progress.emit(90, "正在撰写审查意见通知书...")
            self.signals.log.emit("INFO", "AI 撰写审查意见通知书中...")

            from src.analysis.oa_writer import OAWriter

            writer = OAWriter(self.settings, provider=self.ai_provider)
            oa_markdown = writer.write(
                self.patent_doc, self.comparisons, self.dedup_results)

            self.signals.log.emit("SUCCESS", "审查意见通知书撰写完成")
            self.signals.progress.emit(100, "全部完成")
            self.signals.analysis_done.emit(oa_markdown)
            self.signals.finished.emit(True, "")
        except Exception as e:
            self.signals.error.emit(f"通知书撰写失败: {e}")
            self.signals.log.emit("ERROR", f"通知书撰写失败: {e}")
            self.signals.finished.emit(False, str(e))


class PatentscopeTestWorker(QThread):
    """PATENTSCOPE 快速测试 Worker：搜索N条摘要 → 抓全文 → 返回"""

    def __init__(self, query: str, settings: Settings,
                 max_results: int = 5, parent=None):
        super().__init__(parent)
        self.query = query
        self.settings = settings
        self.max_results = max_results
        self.signals = WorkerSignals()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"PATENTSCOPE 测试失败: {e}")
            self.signals.log.emit("ERROR", f"测试失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.human_behavior import HumanBehavior
        from src.web_automation.patentscope_scraper import PatentscopeScraper

        self.signals.log.emit("INFO", "启动浏览器（无头模式）...")
        self.signals.progress.emit(10, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)

        human = HumanBehavior(self.settings)
        scraper = PatentscopeScraper(page, self.settings, human)

        # 阶段1: 搜索摘要
        self.signals.log.emit("INFO", f"搜索: {self.query}")
        self.signals.progress.emit(30, "搜索摘要...")
        abstracts = await scraper.search_abstracts(
            self.query, max_results=self.max_results, signals=self.signals)

        if not abstracts:
            self.signals.log.emit("WARN", "未找到结果，尝试用简单关键词")
            await browser_mgr.close()
            self.signals.finished.emit(True, "0 条结果")
            return

        self.signals.log.emit("SUCCESS",
            f"找到 {len(abstracts)} 条，开始获取全文...")
        self.signals.progress.emit(50, "获取全文...")

        # 阶段2: 抓全部摘要对应的全文
        self.signals.log.emit("INFO", f"获取全部 {len(abstracts)} 篇全文...")
        enriched = await scraper.fetch_details_batch(abstracts, signals=self.signals)

        await browser_mgr.close()

        full_count = sum(1 for r in enriched if not r.get("_no_detail"))
        self.signals.log.emit("SUCCESS",
            f"测试完成: {len(enriched)} 条 ({full_count} 篇有全文)")

        # 输出摘要
        for i, r in enumerate(enriched):
            pub = r.get("publication_number", "?")
            title = r.get("title", "?")
            claims_len = len(r.get("claims", "") or "")
            desc_len = len(r.get("description", "") or "")
            self.signals.log.emit("INFO",
                f"  [{i+1}] {pub} | {title[:60]} | "
                f"权利要求:{claims_len}字 说明书:{desc_len}字")

        self.signals.progress.emit(100, "测试完成")
        # 用 all_searches_done 传结果
        self.signals.all_searches_done.emit([enriched])
        self.signals.finished.emit(True, "")


class PatentscopeAbstractTestWorker(QThread):
    """PATENTSCOPE 快速摘要测试 Worker：只搜索N条摘要，不抓详情"""

    def __init__(self, query: str, settings: Settings,
                 max_results: int = 5, parent=None):
        super().__init__(parent)
        self.query = query
        self.settings = settings
        self.max_results = max_results
        self.signals = WorkerSignals()

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"PATENTSCOPE 测试失败: {e}")
            self.signals.log.emit("ERROR", f"测试失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.human_behavior import HumanBehavior
        from src.web_automation.patentscope_scraper import PatentscopeScraper

        self.signals.log.emit("INFO", "启动浏览器（无头模式）...")
        self.signals.progress.emit(10, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)

        human = HumanBehavior(self.settings)
        scraper = PatentscopeScraper(page, self.settings, human)

        self.signals.log.emit("INFO", f"搜索摘要: {self.query}")
        self.signals.progress.emit(30, "搜索摘要...")
        abstracts = await scraper.search_abstracts(
            self.query, max_results=self.max_results, signals=self.signals)

        await browser_mgr.close()

        if not abstracts:
            self.signals.log.emit("WARN", "未找到结果")
            self.signals.finished.emit(True, "0 条结果")
            return

        self.signals.log.emit("SUCCESS", f"找到 {len(abstracts)} 条摘要")
        for i, a in enumerate(abstracts):
            self.signals.log.emit("INFO",
                f"  [{i+1}] {a.get('publication_number','?')} | "
                f"{str(a.get('title',''))[:70]} | "
                f"{str(a.get('abstract_snippet',''))[:60]}")

        self.signals.progress.emit(100, "摘要测试完成")
        self.signals.all_searches_done.emit([abstracts])
        self.signals.finished.emit(True, "")


class PatentLookupWorker(QThread):
    """公布号直查 Worker：先搜索拿到 docId，再抓详情"""

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

        mgr = BrowserManager(self.settings)
        context, page = await mgr.launch_with_retry(max_retries=1)

        try:
            human = HumanBehavior(page)
            scraper = PatentscopeScraper(page, self.settings, human)

            q = self.query.replace(" ", "")
            self.signals.log.emit("INFO", f"查询: {q}")

            # 访问搜索页，等表单渲染
            await page.goto(
                self.settings.patentscope_search_url,
                timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # 填表提交
            inp = page.locator("#simpleSearchForm\\:fpSearch\\:input")
            if await inp.count() > 0:
                await inp.fill(self.query)
                btn = page.locator("button[id*='fpSearch']").first
                if await btn.count() > 0:
                    await btn.click()
                    await asyncio.sleep(5)
                    try:
                        await page.wait_for_load_state("load", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(2)

            # 检查是否跳转到详情页
            cur = page.url
            import re as _re
            m = _re.search(r'docId=([^&]+)', cur)
            if m and "detail.jsf" in cur:
                doc_id = m.group(1)
                self.signals.log.emit("INFO", "搜索命中，正在加载详情...")
                result = await scraper._extract_detail_page(doc_id)
            else:
                # 先尝试直连
                result = await scraper.fetch_detail(q)
                if not result:
                    # 最后尝试解析搜索结果
                    try:
                        abstracts = await scraper._parse_results_table()
                        if abstracts:
                            doc_id = abstracts[0].get("doc_id", "")
                            result = await scraper.fetch_detail(doc_id)
                    except Exception:
                        pass

            if not result:
                self.signals.log.emit("WARN", f"未找到: {q}")
                return None

            self.signals.log.emit("SUCCESS",
                f"查询完成: claims={len(result.get('claims',''))} desc={len(result.get('description',''))}")
            return result
        finally:
            await context.close()
