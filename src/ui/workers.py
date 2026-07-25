"""
后台工作线程 - 所有耗时操作在 QThread 中执行
"""
import asyncio
import threading

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
                 ai_provider: str | None = None, parent=None):
        super().__init__(parent)
        self.patent_doc = patent_doc
        self.settings = settings
        self.ai_provider = ai_provider
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.progress.emit(30, "正在生成检索式...")
            self.signals.log.emit("INFO", f"调用 {self.ai_provider or '默认AI'} 生成检索式...")

            from src.query_generator.generator import QueryGenerator
            generator = QueryGenerator(self.settings, provider=self.ai_provider)
            queries = generator.generate(self.patent_doc, max_queries=self.settings.query_max_queries)

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
    统一检索+抓取 Worker
    ——— 同一个浏览器会话完成全部检索和专利详情抓取
    """

    def __init__(self, queries: list, settings: Settings,
                 max_fetch: int = 15, parent=None):
        super().__init__(parent)
        self.queries = queries
        self.settings = settings
        self.max_fetch = max_fetch
        self.signals = WorkerSignals()
        self._is_running = True
        self._fetch_event = threading.Event()
        self._dedup_results = None

    def stop(self):
        self._is_running = False
        self._fetch_event.set()  # 唤醒等待循环，以便退出

    def continue_fetch(self, dedup_results: list):
        """主线程调用：告诉 worker 去重已完成，可以开始抓取详情了"""
        self._dedup_results = dedup_results
        self._fetch_event.set()

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
        from src.web_automation.searcher import Searcher
        from src.web_automation.patent_fetcher import PatentFetcher
        from src.web_automation.human_behavior import HumanBehavior

        # ============ Phase 1: 检索 ============
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

            progress = 55 + int((idx + 1) / len(self.queries) * 25)
            self.signals.progress.emit(progress,
                f"检索式 {idx+1}/{len(self.queries)}")

            himmpat_count, results = await searcher.execute_search(q_str, idx + 1)
            all_results.append(results)

            count_info = f"{himmpat_count}条" if himmpat_count >= 0 else "未知"
            self.signals.log.emit("SUCCESS",
                f"检索式{idx+1}完成: HimmPat显示{count_info}, 提取{len(results)}条")
            self.signals.query_complete.emit(idx + 1, len(self.queries), results)

            if idx < len(self.queries) - 1 and self._is_running:
                await human.inter_search_delay(idx + 1)

        # 保存登录态（后面的抓取阶段可能用到）
        try:
            await browser_mgr.save_storage()
        except Exception:
            pass

        # 通知主线程检索完成，等待去重
        self.signals.progress.emit(80, "检索完成，等待去重...")
        self.signals.all_searches_done.emit(all_results)

        # ============ Phase 1.5: 暂停，等主线程去重 ============
        while not self._fetch_event.is_set() and self._is_running:
            await asyncio.sleep(0.1)

        if not self._is_running or not self._dedup_results:
            self.signals.log.emit("WARN", "未获取到对比文献，跳过详情抓取")
            await browser_mgr.close()
            self.signals.finished.emit(True, "完成（跳过详情抓取）")
            return

        # ============ Phase 2: 抓取专利详情 ============
        top_n = min(len(self._dedup_results), self.max_fetch)
        self.signals.log.emit("INFO",
            f"开始抓取 Top {top_n} 篇专利详情（从当前搜索结果页点击进入）...")
        self.signals.progress.emit(82, "抓取专利详情中...")

        fetcher = PatentFetcher(page, self.settings, human)
        enriched = await fetcher.fetch_batch(
            self._dedup_results,
            signals=self.signals,
            max_count=self.max_fetch,
        )

        self.signals.log.emit("SUCCESS",
            f"专利详情抓取完成: {sum(1 for r in enriched if r.get('full_text'))} 篇获取到全文")
        self.signals.progress.emit(85, "详情抓取完成")

        # 关浏览器
        await browser_mgr.close()

        if self._is_running:
            self.signals.fetch_done.emit(enriched)
            self.signals.finished.emit(True, "")
        else:
            self.signals.finished.emit(True, "用户停止")


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
