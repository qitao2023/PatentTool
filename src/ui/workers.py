"""
后台工作线程 - 所有耗时操作在 QThread 中执行
"""
import asyncio

from PySide6.QtCore import QThread

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


class SearchWorker(QThread):
    """检索执行后台线程（浏览器自动化）"""

    def __init__(self, queries: list, settings: Settings, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.signals = WorkerSignals()
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"检索执行失败: {e}")
            self.signals.log.emit("ERROR", f"检索执行失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.authenticator import Authenticator
        from src.web_automation.searcher import Searcher
        from src.web_automation.human_behavior import HumanBehavior

        self.signals.log.emit("INFO", "启动浏览器...")
        self.signals.progress.emit(45, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)

        # 登录
        auth = Authenticator(page, self.settings)
        self.signals.log.emit("INFO", "检查登录状态...")
        logged_in = await auth.check_login()
        if not logged_in:
            # 根据配置选择自动或手动登录
            if self.settings.himmpat_login_mode == "auto":
                self.signals.log.emit("INFO", "尝试自动登录...")
                logged_in = await auth.auto_login()
            if not logged_in:
                self.signals.log.emit("WARN", "未登录，引导用户登录...")
                self.signals.progress.emit(50, "等待用户登录HimmPat...")
                await auth.manual_login(self.signals)

        self.signals.log.emit("SUCCESS", "HimmPat 登录成功")
        self.signals.progress.emit(55, "开始执行检索...")
        self.signals.login_done.emit(True, "")

        # 逐条检索
        all_results = []
        human = HumanBehavior(self.settings)
        searcher = Searcher(page, self.settings, human)

        for idx, query in enumerate(self.queries):
            if not self._is_running:
                self.signals.log.emit("WARN", "用户停止检索")
                break

            q_str = query.get("query_string", "")
            self.signals.log.emit("INFO",
                f"执行检索式 {idx+1}/{len(self.queries)}: {q_str}")

            # 更新进度
            progress = 55 + int((idx + 1) / len(self.queries) * 25)
            self.signals.progress.emit(progress,
                f"检索式 {idx+1}/{len(self.queries)}")

            himmpat_count, results = await searcher.execute_search(q_str, idx + 1)
            all_results.append(results)

            # 显示HimmPat上的结果数量和实际提取数量
            count_info = f"{himmpat_count}条" if himmpat_count >= 0 else "未知"
            self.signals.log.emit("SUCCESS",
                f"检索式{idx+1}完成: HimmPat显示{count_info}, 提取{len(results)}条")
            self.signals.query_complete.emit(idx + 1, len(self.queries), results)

            # 非最后一次查询时等待间隔
            if idx < len(self.queries) - 1 and self._is_running:
                await human.inter_search_delay(idx + 1)

        # 关闭浏览器
        await browser_mgr.close()

        if self._is_running:
            self.signals.progress.emit(80, "检索全部完成")
            self.signals.log.emit("SUCCESS", f"全部检索完成，共收集 {sum(len(r) for r in all_results)} 条结果")
            self.signals.all_searches_done.emit(all_results)
        else:
            self.signals.finished.emit(True, "用户停止")


class SearchAndFetchWorker(QThread):
    """
    统一检索+抓取 Worker — 使用 HimmPatScraper 一体化流程

    流程（每条检索式独立完成）:
      1. 输入检索式 → 点击检索
      2. 对结果页每个专利: 点击链接 → 进入详情页 → 提取全文 → 返回结果列表
      3. 翻页 → 重复步骤2
      4. 满50条/全部结束 → 下一条检索式
      5. 全部检索式完成后 → 合并去重 → 发出结果

    每条检索式的结果会实时通过 query_complete 信号发出，
    主线程在每条检索式完成后更新 UI。
    """

    def __init__(self, queries: list, settings: Settings,
                 max_fetch: int = 15, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.max_fetch = max_fetch
        self.signals = WorkerSignals()
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"检索分析失败: {e}")
            self.signals.log.emit("ERROR", f"检索分析失败: {e}")
            self.signals.finished.emit(False, str(e))

    async def _run_async(self):
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.authenticator import Authenticator
        from src.web_automation.human_behavior import HumanBehavior
        from src.web_automation.scraper import HimmPatScraper

        # ============ 启动浏览器 + 登录 ============
        self.signals.log.emit("INFO", "启动浏览器...")
        self.signals.progress.emit(45, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)

        auth = Authenticator(page, self.settings)
        self.signals.log.emit("INFO", "检查登录状态...")
        logged_in = await auth.check_login()
        if not logged_in:
            if self.settings.himmpat_login_mode == "auto":
                self.signals.log.emit("INFO", "尝试自动登录...")
                logged_in = await auth.auto_login()
            if not logged_in:
                self.signals.log.emit("WARN", "未登录，引导用户登录...")
                self.signals.progress.emit(50, "等待用户登录HimmPat...")
                await auth.manual_login(self.signals)

        self.signals.log.emit("SUCCESS", "HimmPat 登录成功")
        self.signals.progress.emit(55, "开始执行检索与抓取...")
        self.signals.login_done.emit(True, "")

        # ============ 逐条检索 + 逐条抓取详情（一体化） ============
        human = HumanBehavior(self.settings)
        scraper = HimmPatScraper(page, self.settings, human)

        all_enriched = []  # 每条检索式的 enriched results

        for idx, query in enumerate(self.queries):
            if not self._is_running:
                self.signals.log.emit("WARN", "用户停止检索")
                break

            q_str = query.get("query_string", "")
            angle = query.get("search_angle", "")
            self.signals.log.emit("INFO",
                f"🔍 检索式 {idx+1}/{len(self.queries)} [{angle}]: {q_str}")

            progress = 55 + int((idx + 1) / len(self.queries) * 20)
            self.signals.progress.emit(progress,
                f"检索式 {idx+1}/{len(self.queries)}: 检索+抓取中...")

            # 一体化执行：检索 → 分页 → 逐条点击提取 → 返回 → 翻页
            enriched = await scraper.execute_query(
                q_str,
                query_index=idx + 1,
                max_results=self.settings.himmpat_max_results,
                signals=self.signals,
            )

            all_enriched.append(enriched)

            self.signals.log.emit("SUCCESS",
                f"检索式{idx+1}完成: 获取 {len(enriched)} 篇专利全文")

            # 发出该检索式的结果（用于实时更新 UI）
            self.signals.query_complete.emit(idx + 1, len(self.queries), enriched)

            if idx < len(self.queries) - 1 and self._is_running:
                await human.inter_search_delay(idx + 1)

        # 保存登录态
        try:
            await browser_mgr.save_storage()
        except Exception:
            pass

        # 关闭浏览器
        await browser_mgr.close()

        # ============ 发出全部结果（主线程会做去重 + 分析） ============
        if self._is_running:
            total = sum(len(r) for r in all_enriched)
            self.signals.log.emit("SUCCESS",
                f"全部检索+抓取完成: 共 {total} 篇专利（含重复）")
            self.signals.progress.emit(82, "检索与抓取全部完成")
            self.signals.all_searches_done.emit(all_enriched)
            self.signals.finished.emit(True, "")
        else:
            self.signals.finished.emit(True, "用户停止")


class PatentscopeSearchAndFetchWorker(QThread):
    """
    PATENTSCOPE 三阶段检索 Worker：
      阶段1: 搜索摘要（快，200 条/检索式）
      阶段2: AI 粗筛（从摘要中挑出最相关的 10-20 篇）
      阶段3: 按需抓取全文（只对筛选出的专利拉详情）

    每个阶段的结果保存为 JSON 到 data/output/{专利名}/{时间戳}/
    """

    def __init__(self, queries: list, settings: Settings,
                 patent_doc=None, max_fetch: int = 200,
                 top_n: int = 10, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.patent_doc = patent_doc
        self.max_fetch = max_fetch
        self.top_n = top_n
        self.signals = WorkerSignals()
        self._is_running = True
        self.output_dir = None  # 供 main_window 读取

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
        from src.web_automation.patentscope_scraper import PatentscopeScraper
        from src.analysis.screener import PatentScreener

        # 确定输出目录（专利名 + 时间戳，避免覆盖之前的运行）
        patent_name = ""
        if self.patent_doc:
            patent_name = (self.patent_doc.publication_number
                           or self.patent_doc.title or "unknown")
            import re
            patent_name = re.sub(r'[\\/:*?"<>|]', '_', patent_name)[:80]
        run_dir = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = (Path.cwd() / "data" / "output"
                           / patent_name / run_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_dir = self.output_dir  # 局部引用

        # ============ 阶段0: 启动浏览器 ============
        self.signals.log.emit("INFO", "启动浏览器...")
        self.signals.progress.emit(5, "启动浏览器...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry(max_retries=2)

        self.signals.log.emit("SUCCESS", "浏览器就绪 — PATENTSCOPE 无需登录")

        human = HumanBehavior(self.settings)
        scraper = PatentscopeScraper(page, self.settings, human)

        # ============ 阶段1: 搜索摘要 ============
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO", "阶段1: PATENTSCOPE 搜索摘要")
        self.signals.progress.emit(10, "阶段1: 搜索摘要...")

        all_abstracts = []

        for idx, query in enumerate(self.queries):
            if not self._is_running:
                break

            q_str = query.get("query_string", "")
            angle = query.get("search_angle", "")
            self.signals.log.emit("INFO",
                f"检索式 {idx+1}/{len(self.queries)} [{angle}]: {q_str}")

            progress = 10 + int((idx + 1) / len(self.queries) * 20)
            self.signals.progress.emit(progress,
                f"阶段1: 搜索摘要 {idx+1}/{len(self.queries)}")

            abstracts = await scraper.search_abstracts(
                q_str, max_results=self.max_fetch, signals=self.signals)

            # 标记来源检索式
            for a in abstracts:
                a["source_query"] = q_str

            all_abstracts.append(abstracts)

            self.signals.log.emit("SUCCESS",
                f"检索式{idx+1}: 获取 {len(abstracts)} 篇摘要")

            if idx < len(self.queries) - 1 and self._is_running:
                await human.inter_search_delay(idx + 1)

        # 合并去重
        seen = set()
        unique_abstracts = []
        for batch in all_abstracts:
            for a in batch:
                key = a.get("doc_id") or a.get("publication_number", "")
                if key and key not in seen:
                    seen.add(key)
                    unique_abstracts.append(a)

        total_abstracts = len(unique_abstracts)
        self.signals.log.emit("SUCCESS",
            f"阶段1 完成: 去重后 {total_abstracts} 篇摘要")

        # 保存阶段1结果
        stage1_path = output_dir / "01_search_abstracts.json"
        self._save_json(stage1_path, {
            "stage": "search_abstracts",
            "timestamp": datetime.now().isoformat(),
            "total": total_abstracts,
            "queries": [q.get("query_string", "") for q in self.queries],
            "results": unique_abstracts,
        })
        self.signals.log.emit("INFO", f"  已保存: {stage1_path}")

        # 关闭浏览器（阶段1完成）
        await browser_mgr.close()

        if total_abstracts == 0:
            self.signals.log.emit("WARN", "未找到任何结果")
            self.signals.finished.emit(True, "无结果")
            return

        # 发送摘要结果到 UI
        self.signals.query_complete.emit(1, 1, unique_abstracts)

        # ============ 阶段2: AI 粗筛 ============
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO",
            f"阶段2: AI 从 {total_abstracts} 篇摘要中筛选最相关专利...")
        self.signals.progress.emit(35, "阶段2: AI 粗筛...")

        top_n = min(self.top_n, total_abstracts)

        if self.patent_doc:
            screener = PatentScreener(self.settings)
            screened = screener.screen(self.patent_doc, unique_abstracts, top_n=top_n)
        else:
            # 无本申请信息，直接用原始排序
            screened = unique_abstracts[:top_n]
            for s in screened:
                s["relevance_score"] = 50
                s["relevance_reason"] = "无本申请信息，按原始顺序排列"

        self.signals.log.emit("SUCCESS",
            f"阶段2 完成: AI 筛选出 {len(screened)} 篇（最相关的前 {top_n} 篇）")
        for i, s in enumerate(screened):
            score = s.get("relevance_score", "?")
            reason = s.get("relevance_reason", "")[:60]
            pub = s.get("publication_number", "?")
            self.signals.log.emit("INFO",
                f"  [{i+1}] {pub} (相关度: {score}) {reason}")

        # 保存阶段2结果
        stage2_path = output_dir / "02_ai_screened.json"
        self._save_json(stage2_path, {
            "stage": "ai_screened",
            "timestamp": datetime.now().isoformat(),
            "total_before": total_abstracts,
            "total_after": len(screened),
            "results": screened,
        })
        self.signals.log.emit("INFO", f"  已保存: {stage2_path}")

        # ============ 阶段3: 按需抓取全文 ============
        self.signals.log.emit("INFO", "=" * 40)
        self.signals.log.emit("INFO",
            f"阶段3: 获取 {len(screened)} 篇专利的全文详情...")
        self.signals.progress.emit(45, "阶段3: 启动浏览器获取全文...")

        # 重新启动浏览器
        browser_mgr2 = BrowserManager(self.settings)
        context2, page2 = await browser_mgr2.launch_with_retry(max_retries=2)
        human2 = HumanBehavior(self.settings)
        scraper2 = PatentscopeScraper(page2, self.settings, human2)

        enriched = await scraper2.fetch_details_batch(
            screened, signals=self.signals,
            stop_check=lambda: not self._is_running)

        await browser_mgr2.close()

        full_count = sum(1 for r in enriched if not r.get("_no_detail"))
        self.signals.log.emit("SUCCESS",
            f"阶段3 完成: {full_count}/{len(enriched)} 篇获取到全文")

        # 保存阶段3结果
        stage3_path = output_dir / "03_full_details.json"
        self._save_json(stage3_path, {
            "stage": "full_details",
            "timestamp": datetime.now().isoformat(),
            "total": len(enriched),
            "full_text_count": full_count,
            "results": enriched,
        })
        self.signals.log.emit("INFO", f"  已保存: {stage3_path}")

        # ============ 阶段3.5: AI 通读全文 + 相关度打分 ============
        if self._is_running and enriched and self.patent_doc:
            self.signals.log.emit("INFO", "=" * 40)
            self.signals.log.emit("INFO",
                f"阶段3.5: AI 通读 {len(enriched)} 篇全文，评估相关度...")
            self.signals.progress.emit(50, "阶段3.5: AI 评估全文相关度...")

            screener2 = PatentScreener(self.settings)
            enriched = screener2.score_full_text(
                self.patent_doc, enriched, signals=self.signals)

            # 保存打分后的结果
            stage35_path = output_dir / "03.5_fulltext_scored.json"
            self._save_json(stage35_path, {
                "stage": "fulltext_scored",
                "timestamp": datetime.now().isoformat(),
                "total": len(enriched),
                "results": enriched,
            })
            self.signals.log.emit("INFO", f"  已保存: {stage35_path}")
        elif not self.patent_doc:
            self.signals.log.emit("WARN",
                "跳过分: 无本申请信息，使用摘要阶段的评分")

        # ============ 完成 ============
        if self._is_running:
            self.signals.log.emit("SUCCESS",
                f"PATENTSCOPE 检索完成: {full_count} 篇全文")
            self.signals.log.emit("INFO",
                f"所有结果已保存到: {output_dir}")
            self.signals.progress.emit(55, "检索完成，准备分析...")
            self.signals.all_searches_done.emit([enriched])
            self.signals.finished.emit(True, "")
        else:
            self.signals.finished.emit(True, "用户停止")

    @staticmethod
    def _save_json(path, data):
        import json as json_module
        with open(path, "w", encoding="utf-8") as f:
            json_module.dump(data, f, indent=2, ensure_ascii=False, default=str)


class PatentFetchWorker(QThread):
    """专利详情抓取后台线程"""

    def __init__(self, dedup_results: list, settings: Settings,
                 max_fetch: int = 15, parent=None):
        super().__init__(parent)
        self.dedup_results = dedup_results
        self.settings = settings
        self.max_fetch = max_fetch
        self.signals = WorkerSignals()
        self._is_running = True

    def stop(self):
        self._is_running = False

    def run(self):
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
            loop.close()
        except Exception as e:
            self.signals.error.emit(f"专利详情抓取失败: {e}")
            self.signals.log.emit("ERROR", f"专利详情抓取失败: {e}")
            # 即使失败也继续（用已有数据）
            self.signals.fetch_done.emit(self.dedup_results)

    async def _run_async(self):
        from src.web_automation.browser_manager import BrowserManager
        from src.web_automation.authenticator import Authenticator
        from src.web_automation.patent_fetcher import PatentFetcher
        from src.web_automation.human_behavior import HumanBehavior

        self.signals.log.emit("INFO", f"启动浏览器，准备抓取专利详情（最多{self.max_fetch}篇）...")
        self.signals.progress.emit(78, "抓取专利详情中...")

        browser_mgr = BrowserManager(self.settings)
        context, page = await browser_mgr.launch_with_retry()

        # 检查登录
        auth = Authenticator(page, self.settings)
        logged_in = await auth.check_login()
        if not logged_in:
            if self.settings.himmpat_login_mode == "auto":
                logged_in = await auth.auto_login()
            if not logged_in:
                self.signals.log.emit("WARN", "需要登录HimmPat...")
                await auth.manual_login(self.signals)

        # 抓取详情
        human = HumanBehavior(self.settings)
        fetcher = PatentFetcher(page, self.settings, human)
        enriched = await fetcher.fetch_batch(
            self.dedup_results,
            signals=self.signals,
            max_count=self.max_fetch,
        )

        await browser_mgr.close()

        self.signals.log.emit("SUCCESS",
            f"专利详情抓取完成: {sum(1 for r in enriched if r.get('full_text'))} 篇获取到全文")
        self.signals.progress.emit(82, "详情抓取完成")
        self.signals.fetch_done.emit(enriched)

        if not self._is_running:
            self.signals.finished.emit(True, "用户停止")


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


class SingleCompareWorker(QThread):
    """单篇专利对比 Worker — 点击左侧专利时触发，在右侧显示详细对比"""

    def __init__(self, patent_doc, candidate: dict, settings: Settings,
                 ai_provider: str | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.candidate = candidate
        self.settings = settings
        self.ai_provider = ai_provider
        self.signals = WorkerSignals()

    def run(self):
        try:
            pub = self.candidate.get("publication_number", "?")
            self.signals.log.emit("INFO", f"🔄 AI 正在对比 {pub} vs 本申请...")

            from src.analysis.comparator import PatentComparator
            comparator = PatentComparator(
                self.settings, provider=self.ai_provider)
            result = comparator.compare_single_fulltext(
                self.patent_doc, self.candidate)

            self.signals.log.emit("SUCCESS", f"对比完成: {pub}")
            # 用 analysis_done 传结果给主线程
            self.signals.analysis_done.emit(result)
            self.signals.finished.emit(True, "")
        except Exception as e:
            self.signals.error.emit(f"对比失败: {e}")
            self.signals.log.emit("ERROR", f"对比失败: {e}")
            self.signals.finished.emit(False, str(e))
