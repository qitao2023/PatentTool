"""
主窗口 - 整合所有面板
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QSplitter, QWidget,
    QStatusBar, QLabel, QMessageBox, QApplication,
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction

from src.ui.input_panel import InputPanel
from src.ui.log_panel import LogPanel
from src.ui.result_panel import ResultPanel
from src.ui.report_panel import ReportPanel
from src.ui.dialogs import SettingsDialog
from src.ui.workers import PDFParseWorker, QueryGenerateWorker, SearchAndFetchWorker, AnalysisWorker
from src.utils.config import Settings
from src.utils.signals import WorkerSignals


class MainWindow(QMainWindow):
    """应用主窗口"""

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._patent_doc = None
        self._queries = None
        self._all_raw_results = None
        self._dedup_results = None
        self._current_worker = None
        self._user_params = {"ai_provider": "deepseek"}

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()

    def _setup_ui(self):
        self.setWindowTitle("专利检索分析工具 v1.0")
        self.setMinimumSize(1400, 900)
        self.resize(1600, 1000)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # ① 输入面板
        self.input_panel = InputPanel()
        self.input_panel.start_clicked.connect(self._on_start)
        self.input_panel.stop_clicked.connect(self._on_stop)
        self.input_panel.reset_clicked.connect(self._on_reset)
        self.input_panel.test_clicked.connect(self._on_test)
        self.input_panel.settings_clicked.connect(self._on_open_settings)
        main_layout.addWidget(self.input_panel)

        # ② 日志面板
        self.log_panel = LogPanel()
        main_layout.addWidget(self.log_panel)

        # ③④ 结果列表 + 报告（水平分割）
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.result_panel = ResultPanel()
        self.report_panel = ReportPanel()
        splitter.addWidget(self.result_panel)
        splitter.addWidget(self.report_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 5)
        main_layout.addWidget(splitter, 1)

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        open_action = QAction("打开PDF...", self)
        open_action.triggered.connect(self._menu_open_pdf)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        settings_action = QAction("设置...", self)
        settings_action.triggered.connect(self._on_open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        run_menu = menubar.addMenu("运行(&R)")
        start_action = QAction("开始分析", self)
        start_action.triggered.connect(self._on_start)
        run_menu.addAction(start_action)
        stop_action = QAction("停止", self)
        stop_action.triggered.connect(self._on_stop)
        run_menu.addAction(stop_action)

        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_statusbar(self):
        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("就绪")
        status.addWidget(self.status_label, 1)
        self.session_label = QLabel("")
        status.addPermanentWidget(self.session_label)

    def _show_about(self):
        QMessageBox.about(self, "关于 专利检索分析工具",
            "<h3>专利检索分析工具 v1.0</h3>"
            "<p>AI驱动的专利PDF检索、对比分析和撰写辅助工具。</p>"
            "<p>技术栈: PySide6 + DeepSeek/Kimi + Playwright</p>"
        )

    def _on_test(self):
        """运行HimmPat连接测试"""
        self.log_panel.append_log("INFO", "=" * 50)
        self.log_panel.append_log("INFO", "启动HimmPat连接测试...")
        self.log_panel.append_log("INFO", "测试将在独立窗口中运行，请查看终端输出")
        import subprocess, sys
        subprocess.Popen(
            [sys.executable, "-m", "src.test_himm"],
            cwd=str(Path(__file__).parent.parent.parent),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def _on_open_settings(self):
        """打开设置对话框"""
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self, provider: str, model: str, temperature: float):
        """设置保存后的回调"""
        self._user_params["ai_provider"] = provider
        self._user_params["ai_model"] = model
        self._user_params["temperature"] = temperature
        self.input_panel.set_ai_provider(provider)
        self.log_panel.append_log("INFO",
            f"AI设置已更新: {provider} / {model} / temp={temperature}")

    def _menu_open_pdf(self):
        self.input_panel._on_browse()

    # --- 控制按钮事件 ---

    def _on_start(self):
        """点击「开始分析」"""
        pdf_path = self.input_panel.get_pdf_path()
        if not pdf_path:
            QMessageBox.warning(self, "提示", "请先选择专利PDF文件")
            return

        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{pdf_path}")
            return
        if pdf_file.suffix.lower() not in (".pdf",):
            QMessageBox.warning(self, "提示", "请选择 PDF 文件")
            return

        # 保存界面参数
        self._user_params = self.input_panel.get_params()
        ai_provider = self._user_params.get("ai_provider", "deepseek")
        self.log_panel.append_log("INFO", f"AI引擎: {ai_provider}")

        # 重置界面
        self._on_reset()
        self._user_params = {"ai_provider": ai_provider}
        self.input_panel.set_running_state(True)
        self.session_label.setText("会话进行中...")

        # 启动PDF解析 Worker
        self.log_panel.append_log("INFO", "=" * 50)
        self.log_panel.append_log("INFO", "开始专利检索分析流程")

        self._run_pdf_parse(pdf_path)

    def _on_stop(self):
        """点击「停止」"""
        if self._current_worker and hasattr(self._current_worker, "stop"):
            self._current_worker.stop()
        self.log_panel.append_log("WARN", "用户手动停止")
        self.input_panel.set_running_state(False)
        self.status_label.setText("已停止")

    def _on_reset(self):
        """点击「重置」"""
        if self._current_worker and self._current_worker.isRunning():
            if hasattr(self._current_worker, "stop"):
                self._current_worker.stop()
            self._current_worker.quit()
            self._current_worker.wait(2000)

        self._patent_doc = None
        self._queries = None
        self._all_raw_results = None
        self._dedup_results = None
        self._current_worker = None

        self.input_panel.reset()
        self.log_panel.reset()
        self.result_panel.reset()
        self.report_panel.reset()
        self.status_label.setText("已重置")

    # --- 工作流串联 ---

    def _run_pdf_parse(self, pdf_path: str):
        self.status_label.setText("正在解析PDF...")
        self._current_worker = PDFParseWorker(pdf_path, self.settings)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.pdf_done.connect(self._on_pdf_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    @Slot(object)
    def _on_pdf_done(self, patent_doc):
        self._patent_doc = patent_doc
        self.log_panel.append_log("SUCCESS", f"标题: {patent_doc.title}")
        # 继续：生成检索式
        self._run_query_generate(patent_doc)

    def _run_query_generate(self, patent_doc):
        ai_provider = self._user_params.get("ai_provider", "deepseek")
        self.status_label.setText("正在生成检索式...")
        self._current_worker = QueryGenerateWorker(
            patent_doc, self.settings, ai_provider=ai_provider)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.queries_done.connect(self._on_queries_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    @Slot(list)
    def _on_queries_done(self, queries):
        self._queries = queries
        # 继续：执行检索
        self._run_search(queries)

    def _run_search(self, queries):
        self.status_label.setText("正在执行检索...")
        top_n = self.settings.analysis_top_n
        self._current_worker = SearchAndFetchWorker(queries, self.settings, max_fetch=top_n)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.query_complete.connect(self.result_panel.add_query_results)
        w.signals.login_done.connect(self._on_login_done)
        w.signals.all_searches_done.connect(self._on_all_searches_done)
        w.signals.fetch_done.connect(self._on_fetch_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    @Slot(bool, str)
    def _on_login_done(self, success, msg):
        if not success:
            self.log_panel.append_log("ERROR", f"登录失败: {msg}")

    @Slot(list)
    def _on_all_searches_done(self, all_results):
        self._all_raw_results = all_results
        # 去重
        self._run_dedup(all_results)

    def _run_dedup(self, all_results):
        self.status_label.setText("正在去重...")
        self.log_panel.append_log("INFO", "开始去重...")

        from src.result_collector.deduplicator import Deduplicator
        deduper = Deduplicator(self.settings)
        deduped, removed = deduper.deduplicate(all_results)

        self._dedup_results = deduped
        self.log_panel.append_log("SUCCESS",
            f"去重完成: 原始 {sum(len(r) for r in all_results)} 条 → 去重后 {len(deduped)} 条 (移除 {removed} 条)")
        self.result_panel.show_dedup_results(deduped)

        # 继续：用当前浏览器从搜索结果页点击进入专利详情
        if self._patent_doc and deduped:
            top_n = self.settings.analysis_top_n
            self.log_panel.append_log("INFO",
                f"开始从搜索结果页抓取 Top {top_n} 篇专利详情...")
            self.status_label.setText("抓取专利详情中...")
            if hasattr(self._current_worker, 'continue_fetch'):
                self._current_worker.continue_fetch(deduped)
        else:
            self.log_panel.append_log("WARN", "没有对比文献或专利文档，跳过分析")
            self.log_panel.update_progress(100, "完成（无分析）")
            self.input_panel.set_running_state(False)
            self.status_label.setText("完成")

    def _on_fetch_done(self, enriched_results):
        """详情抓取完成，进入分析"""
        self.log_panel.append_log("SUCCESS",
            f"已获取 {sum(1 for r in enriched_results if r.get("full_text"))} 篇专利全文")
        if self._patent_doc:
            self._run_analysis(self._patent_doc, enriched_results)

    def _run_analysis(self, patent_doc, dedup_results):
        ai_provider = self._user_params.get("ai_provider", "deepseek")
        self.status_label.setText("正在进行对比分析...")
        self._current_worker = AnalysisWorker(
            patent_doc, dedup_results, self.settings, ai_provider=ai_provider)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.analysis_done.connect(self.report_panel.show_report)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    @Slot(str)
    def _handle_error(self, error_msg: str):
        self.log_panel.append_log("ERROR", error_msg)

    @Slot(bool, str)
    def _on_worker_finished(self, success: bool, msg: str):
        if success:
            self.log_panel.append_log("SUCCESS", "当前阶段完成")
            self.status_label.setText("完成")
        else:
            self.log_panel.append_log("ERROR", f"阶段失败: {msg}")
            self.input_panel.set_running_state(False)
            self.status_label.setText("失败")

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        if self._current_worker and self._current_worker.isRunning():
            if hasattr(self._current_worker, "stop"):
                self._current_worker.stop()
            self._current_worker.quit()
            self._current_worker.wait(3000)
        event.accept()
