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
from src.ui.workers import (
    PDFParseWorker, QueryGenerateWorker,
    PatentscopeSearchAndFetchWorker,
    AnalysisWorker, OAWriterWorker,
    PatentLookupWorker,
    MultiQueryTestWorker, FinalReviewWorker,
    ApplicationDateWorker,
)
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
        self._pdf_path = None       # 用户选择的 PDF 路径
        self._output_dir = None     # 本次运行输出目录
        self._comparison_cache = {} # 对比结果缓存 {pub: markdown}
        self._analysis_report = None
        self._date_extract_seq = 0  # 申请日提取任务序号，用于丢弃过期结果
        self._date_worker = None    # 当前申请日提取线程
        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()
        self._auto_extract_default_date()

    def _setup_ui(self):
        self.setWindowTitle("专利检索分析工具 v1.0")
        self.setMinimumSize(1024, 700)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        # ① 输入面板
        self.input_panel = InputPanel()
        # 从配置加载检索式数量
        self.input_panel.max_queries_spin.setRange(1, 50)
        self.input_panel.max_queries_spin.setValue(self.settings.query_max_queries)
        self.input_panel.start_clicked.connect(self._on_start)
        self.input_panel.stop_clicked.connect(self._on_stop)
        self.input_panel.clear_log_clicked.connect(self._on_clear_log)
        self.input_panel.settings_clicked.connect(self._on_open_settings)
        self.input_panel.file_selected.connect(self._on_file_selected)
        self.input_panel.open_existing.connect(self._on_open_existing)
        self.input_panel.extract_date_clicked.connect(self._on_extract_date_clicked)
        main_layout.addWidget(self.input_panel)

        # ② 日志面板（可拖拽高度）
        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(120)

        # ③④ 结果列表 + 报告
        bottom_splitter = QSplitter(Qt.Orientation.Horizontal)
        bottom_splitter.setHandleWidth(3)
        self.result_panel = ResultPanel()
        self.result_panel.patent_selected.connect(self._on_patent_clicked)
        self.report_panel = ReportPanel()
        bottom_splitter.addWidget(self.result_panel)
        bottom_splitter.addWidget(self.report_panel)
        bottom_splitter.setStretchFactor(0, 3)
        bottom_splitter.setStretchFactor(1, 4)

        # 垂直分割：日志在上，结果+报告在下
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.setHandleWidth(4)
        main_splitter.addWidget(self.log_panel)
        main_splitter.addWidget(bottom_splitter)
        main_splitter.setStretchFactor(0, 0)  # 日志默认紧凑
        main_splitter.setStretchFactor(1, 1)  # 结果区占满剩余
        main_splitter.setSizes([180, 600])
        main_layout.addWidget(main_splitter, 1)

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
        history_action = QAction("历史记录...", self)
        history_action.triggered.connect(self._on_open_history)
        file_menu.addAction(history_action)
        file_menu.addSeparator()
        prompts_action = QAction("提示词配置...", self)
        prompts_action.triggered.connect(self._on_open_prompt_editor)
        file_menu.addAction(prompts_action)
        file_menu.addSeparator()
        final_review_action = QAction("终选评述...", self)
        final_review_action.triggered.connect(self._on_final_review)
        file_menu.addAction(final_review_action)
        file_menu.addSeparator()
        oa_action = QAction("撰写通知书...", self)
        oa_action.triggered.connect(self._on_oa_write_clicked)
        file_menu.addAction(oa_action)
        file_menu.addSeparator()
        test_action = QAction("测试工具...", self)
        test_action.triggered.connect(self._on_open_test)
        file_menu.addAction(test_action)
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

    def _on_open_test(self):
        """打开测试工具对话框"""
        from src.ui.dialogs import TestDialog
        dlg = TestDialog(self, settings=self.settings,
                         max_results=self.input_panel.max_results_spin.value())
        dlg.lookup_patent.connect(self._on_lookup_patent)
        dlg.batch_test.connect(self._on_batch_test)
        dlg.exec()

    def _on_lookup_patent(self, doc_id: str):
        """公布号直查（从测试对话框触发）"""
        if not doc_id.strip():
            return
        self.log_panel.append_log("INFO", f"公布号查询: {doc_id}")
        self.input_panel.set_running_state(True)
        self.status_label.setText(f"查询 {doc_id}...")
        self._current_worker = PatentLookupWorker(doc_id, self.settings)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.lookup_done.connect(self._on_lookup_done)
        w.start()

    @Slot(dict)
    def _on_lookup_done(self, patent: dict):
        """直查结果返回"""
        self.result_panel.show_dedup_results([patent])
        self.report_panel.show_patent_detail(patent)
        self.input_panel.set_running_state(False)
        self.status_label.setText("查询完成")

    @Slot(list, str, int, int)
    def _on_batch_test(self, queries: list[str], test_name: str,
                       max_results: int, concurrency: int):
        """批量检索测试：搜索 → 去重 → 下载 → 报告（零 AI）"""
        if not queries:
            return
        eng_name = ("Google Patents" if self.settings.search_source == "google"
                    else "PATENTSCOPE (WIPO)")
        self.log_panel.append_log("INFO", "=" * 50)
        self.log_panel.append_log("INFO",
            f"批量检索测试: {len(queries)} 个检索式")
        self.log_panel.append_log("SUCCESS", f"检索引擎: {eng_name}")
        for i, q in enumerate(queries, 1):
            self.log_panel.append_log("INFO", f"  [{i}] {q[:100]}")
        self.input_panel.set_running_state(True)
        self.status_label.setText(
            f"批量测试 [{eng_name}] ({len(queries)} 检索式 × {max_results} 条)...")
        self._current_worker = MultiQueryTestWorker(
            queries=queries,
            settings=self.settings,
            test_name=test_name,
            max_results=max_results,
            concurrency=concurrency,
        )
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.all_searches_done.connect(self._on_test_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    def _on_test_done(self, results):
        """测试完成"""
        total = sum(len(r) for r in results)
        self.log_panel.append_log("SUCCESS",
            f"测试完成: 获取 {total} 篇专利")
        self.input_panel.set_running_state(False)
        self.status_label.setText("测试完成")
        # 填充表格 + 第一条显示详情
        flat = []
        for batch in results:
            flat.extend(batch)
        if flat:
            self.result_panel.show_dedup_results(flat)
            self.report_panel.show_patent_detail(flat[0])
        # 保存测试结果
        from datetime import datetime
        out = (Path.cwd() / "data" / "output"
               / "test" / datetime.now().strftime("%Y%m%d_%H%M%S"))
        out.mkdir(parents=True, exist_ok=True)
        import json as json_module
        with open(out / "test_results.json", "w", encoding="utf-8") as f:
            json_module.dump(results, f, indent=2, ensure_ascii=False, default=str)
        self.log_panel.append_log("INFO", f"结果已保存: {out / 'test_results.json'}")

    def _on_open_settings(self):
        """打开设置对话框"""
        dlg = SettingsDialog(self.settings, self)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self, provider: str, model: str, temperature: float,
                            params: dict):
        """设置保存后的回调"""
        self._user_params["ai_provider"] = provider
        self._user_params["ai_model"] = model
        self._user_params["temperature"] = temperature
        self._user_params.update(params)
        self.input_panel.set_ai_provider(provider)
        self.log_panel.append_log("INFO",
            f"设置已更新: {provider} / {model} / temp={temperature} "
            f"| 参数: {params}")

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

        self._pdf_path = pdf_path  # 存储，后续建输出目录用

        # 合并参数：界面数值 + 设置对话框的复选框状态
        self._user_params = self.input_panel.get_params()
        self._user_params.update({
            "include_citations": self.settings.search_include_citations,
            "force_refresh": self.settings.search_force_refresh,
            "stop_after": self.settings.search_stop_after,
            "prefer_cn_family": self.settings.search_prefer_cn_family,
        })
        stop_labels = {
            "abstracts": "搜完摘要", "screen": "下载前", "download": "下载后",
            "score": "评分后", "full": "全程"
        }
        cn_family = "开启" if self._user_params.get("prefer_cn_family", True) else "关闭"
        self.log_panel.append_log("INFO",
            f"检索设置: {self._user_params.get('max_queries', 3)}个检索式 "
            f"× {self._user_params.get('max_results', 100)}条/检索式 "
            f"| AI: {self._user_params.get('ai_provider', 'deepseek')} "
            f"| 断点: {stop_labels.get(self._user_params.get('stop_after','full'), '全程')} "
            f"| CN同族优先: {cn_family}")

        # 保存界面设置（reset 会清掉）
        saved_queries = self.input_panel.max_queries_spin.value()
        saved_results = self.input_panel.max_results_spin.value()
        saved_fetch = self.input_panel.fetch_detail_spin.value()
        saved_app_date = self.input_panel.application_date_edit.text().strip()

        # 重置界面
        self._reset_state()

        # 恢复界面设置
        self.input_panel.path_edit.setText(pdf_path)
        self.input_panel.max_queries_spin.setValue(saved_queries)
        self.input_panel.max_results_spin.setValue(saved_results)
        self.input_panel.fetch_detail_spin.setValue(saved_fetch)
        self.input_panel.application_date_edit.setText(saved_app_date)
        self.input_panel.set_running_state(True)
        self.session_label.setText("会话进行中...")

        # 启动PDF解析 Worker
        self.log_panel.append_log("INFO", "=" * 50)
        self.log_panel.append_log("INFO", "开始专利检索分析流程")

        self._run_pdf_parse(pdf_path)

    @Slot(str)
    def _on_file_selected(self, pdf_path: str):
        """PDF 选择后扫描历史运行 + 自动提取申请日"""
        from src.utils.paths import scan_runs
        runs = scan_runs(pdf_path)
        self.input_panel.show_runs(runs)
        if runs:
            self.log_panel.append_log("INFO",
                f"发现 {len(runs)} 次历史运行")
        self._run_application_date_extract(pdf_path)

    # --- 申请日提取（选择PDF自动 + 点「提取」手动） ---

    def _run_application_date_extract(self, pdf_path: str):
        """启动申请日轻量提取（后台线程，不阻塞界面）。

        用自增序号标记任务：期间又选了别的 PDF 时，过期结果直接丢弃。
        """
        path = (pdf_path or "").strip()
        if not path or not Path(path).exists():
            return
        if self._date_worker and self._date_worker.isRunning():
            self._date_worker.wait(2000)

        self._date_extract_seq += 1
        seq = self._date_extract_seq
        w = ApplicationDateWorker(path)
        w.extracted.connect(
            lambda d, s=seq: self._on_application_date_extracted(d, s))
        w.error.connect(
            lambda msg, s=seq: self._on_application_date_error(msg, s))
        self._date_worker = w
        w.start()

    def _on_application_date_extracted(self, date_str: str, seq: int):
        if seq != self._date_extract_seq:
            return  # 过期结果：期间又选了别的 PDF
        if date_str:
            self.log_panel.append_log("INFO",
                f"已从PDF提取申请日: {date_str}")
        else:
            self.log_panel.append_log("WARN",
                "未从PDF提取到申请日，请在界面手动填写")
        self.input_panel.apply_extracted_date(date_str)

    def _on_application_date_error(self, msg: str, seq: int):
        if seq != self._date_extract_seq:
            return
        self.log_panel.append_log("WARN", msg)
        self.input_panel.show_extract_failed()

    def _on_extract_date_clicked(self):
        """点「提取」按钮：对当前路径重新提取。"""
        path = self.input_panel.get_pdf_path()
        if not path:
            QMessageBox.warning(self, "提示", "请先选择有效的PDF文件")
            return
        if not Path(path).exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{path}")
            return
        self._run_application_date_extract(path)

    def _auto_extract_default_date(self):
        """启动时若已预填默认测试 PDF 路径，自动提取一次申请日。"""
        path = self.input_panel.get_pdf_path()
        if path and Path(path).exists():
            self._run_application_date_extract(path)

    def _on_open_history(self):
        """打开历史记录浏览对话框"""
        from src.ui.history_dialog import HistoryDialog
        dlg = HistoryDialog(parent=self)
        dlg.run_selected.connect(self._on_open_existing)
        dlg.exec()

    def _on_open_prompt_editor(self):
        """打开提示词编辑器"""
        from src.ui.dialogs import PromptEditorDialog
        dlg = PromptEditorDialog(self.settings, self)
        dlg.prompt_saved.connect(self._on_prompts_saved)
        dlg.exec()

    def _on_prompts_saved(self, profile: str):
        """提示词保存后的回调"""
        self.log_panel.append_log("INFO", f"提示词方案「{profile}」已保存，下次检索生效")

    @Slot(str)
    def _on_open_existing(self, run_path: str):
        """打开已有运行结果，完整重建状态（结果列表 + 报告 + 对比缓存）"""
        import json
        from pathlib import Path
        rp = Path(run_path)
        self.log_panel.append_log("INFO", f"打开已有结果: {rp.name}")
        self._output_dir = rp  # 设置输出目录，确保后续保存走对路径

        # ── 加载对比缓存 ──────────────────────────────────────────
        self._comparison_cache = {}
        cache_path = rp / "comparison_cache.json"
        if cache_path.exists():
            try:
                self._comparison_cache = json.loads(cache_path.read_text(encoding="utf-8"))
                self.log_panel.append_log("INFO",
                    f"  已加载对比缓存: {len(self._comparison_cache)} 篇")
            except Exception:
                pass

        # ── 加载各阶段 JSON ────────────────────────────────────────
        loaded = {}
        stage_files = [
            ("01_search_abstracts.json", "abstracts"),
            ("03_ai_screened.json", "screened"),
            ("03_full_details.json", "details"),
            ("03.5_fulltext_scored.json", "scored"),
        ]
        for filename, key in stage_files:
            fpath = rp / filename
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        loaded[key] = json.load(f)
                except Exception:
                    pass

        # ── 显示结果列表 ───────────────────────────────────────────
        results = []
        if "screened" in loaded and "results" in loaded["screened"]:
            results = loaded["screened"]["results"]
        elif "abstracts" in loaded and "results" in loaded["abstracts"]:
            results = loaded["abstracts"]["results"]

        if results:
            self.result_panel.show_results(results)
            self.log_panel.append_log("INFO", f"  已加载 {len(results)} 篇专利结果")

        # ── 显示分析报告 ───────────────────────────────────────────
        md_path = rp / "04_analysis_report.md"
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8")
            class FakeReport:
                pass
            report = FakeReport()
            report.markdown_content = md_text
            report.html_content = (rp / "04_analysis_report.html").read_text(
                encoding="utf-8") if (rp / "04_analysis_report.html").exists() else ""
            report.comparisons = []  # 对比缓存已单独加载
            self._analysis_report = report
            self.report_panel.show_report(report)
            self.log_panel.append_log("INFO", "  已加载分析报告")

        # ── 显示日志文件 ───────────────────────────────────────────
        log_path = rp / "run.log"
        if log_path.exists():
            self.log_panel.append_log("INFO", f"  运行日志: {log_path}")
            # 可选：加载历史日志到 UI（追加方式）
            try:
                for line in log_path.read_text(encoding="utf-8").splitlines()[-20:]:
                    self.log_panel.append_log("DEBUG", line.split("] ", 1)[-1] if "] " in line else line)
            except Exception:
                pass

        # ── OA 通知书 ──────────────────────────────────────────────
        oa_path = rp / "05_审查意见通知书.md"
        if oa_path.exists():
            self.log_panel.append_log("INFO",
                f"  审查意见通知书: {oa_path}")

        self.log_panel.append_log("SUCCESS",
            f"已加载完整运行记录: {len(results)} 篇结果, "
            f"{len(self._comparison_cache)} 篇对比缓存")

    def _on_stop(self):
        """点击「停止」"""
        if self._current_worker and hasattr(self._current_worker, "stop"):
            self._current_worker.stop()
        self.log_panel.append_log("WARN", "用户手动停止")
        self.input_panel.set_running_state(False)
        self.status_label.setText("已停止")

    def _on_clear_log(self):
        """点击「清空日志」"""
        self.log_panel.clear_log()

    def _reset_state(self):
        """开始新一轮分析前重置界面与状态（内部使用）"""
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
        self._comparison_cache = {}
        self._analysis_report = None
        self._output_dir = None
        # 注意: _force_debug_search 不在此重置，它由 _on_search_abstract 设置，
        # 在 _on_all_searches_done 或 _run_search 中消费后重置。

        self.input_panel.reset()
        self.log_panel.reset()
        self.result_panel.reset()
        self.report_panel.reset()
        self.status_label.setText("就绪")

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

    def _apply_manual_dates(self, patent_doc):
        """手动录入的申请日覆盖 PDF 解析结果（兜底：PDF 首页没提出来时用）。"""
        manual = (self._user_params.get("application_date") or "").strip()
        if not manual:
            return
        patent_doc.application_date = manual
        self.log_panel.append_log("INFO",
            f"已应用手动申请日: {manual}")

    def _engine_name(self) -> str:
        """按 search_source 返回实际检索引擎显示名，供日志/状态栏使用。"""
        return ("Google Patents" if self.settings.search_source == "google"
                else "PATENTSCOPE (WIPO)")

    @Slot(object)
    def _on_pdf_done(self, patent_doc):
        self._apply_manual_dates(patent_doc)
        self._patent_doc = patent_doc
        pub_num = patent_doc.publication_number or ""

        # 确定输出目录
        from src.utils.paths import get_output_dir
        self._output_dir = get_output_dir(self._pdf_path, patent_doc)
        self.log_panel.append_log("INFO",
            f"输出目录: {self._output_dir}")
        # 设置日志文件，所有日志同步落盘
        self.log_panel.set_log_file(str(self._output_dir / "run.log"))

        if pub_num:
            force_refresh = self._user_params.get("force_refresh", False)
            cache_path = None

            # 检查本地缓存（除非强制刷新）
            if self._pdf_path and not force_refresh:
                import json
                from src.web_automation.patentscope_scraper import is_cached_patent_valid
                cache_path = Path(self._pdf_path).parent / f"本申请_{pub_num}.json"
                if cache_path.exists():
                    try:
                        data = json.loads(cache_path.read_text(encoding="utf-8"))
                        if is_cached_patent_valid(data):
                            self.log_panel.append_log("INFO",
                                f"本申请信息已缓存，直接使用: {cache_path.name}")
                            self._on_main_lookup_done(data)
                            return
                        else:
                            self.log_panel.append_log("WARN",
                                "缓存数据不完整，重新查询...")
                    except Exception:
                        self.log_panel.append_log("WARN", "缓存文件损坏，重新查询...")

            # 无缓存或强制刷新 → 在线获取（引擎由 search_source 决定）
            self.log_panel.append_log("INFO",
                f"PDF中获取公布号: {pub_num}，从 {self._engine_name()} 联网查询完整信息...")
            self._run_lookup_for_main(pub_num)
        else:
            # 无公布号 → 降级用 PDF 解析结果
            self.log_panel.append_log("WARN",
                "PDF中未找到公布号，使用PDF解析结果（可能不完整）")
            self.log_panel.append_log("SUCCESS", f"标题: {patent_doc.title}")
            self._run_query_generate(patent_doc)

    def _run_lookup_for_main(self, pub_num: str):
        """主流程中的公布号查詢：获取完整信息后继续"""
        self.status_label.setText(
            f"正在 {self._engine_name()} 查询 {pub_num} 的完整信息...")
        self._current_worker = PatentLookupWorker(
            pub_num, self.settings)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.lookup_done.connect(self._on_main_lookup_done)
        w.start()

    @Slot(dict)
    def _on_main_lookup_done(self, data: dict):
        """公布号查全完成后，转换为 PatentDocument 继续流程"""
        from src.pdf_extractor.extractor import PatentDocument

        # 申请日/优先权日：查全数据优先，PDF 解析结果兜底
        pdf_doc = self._patent_doc
        application_date = (data.get("application_date")
                            or (getattr(pdf_doc, "application_date", "")
                                if pdf_doc else "")
                            or "")
        priority_date = (data.get("priority_date")
                         or (getattr(pdf_doc, "priority_date", "")
                             if pdf_doc else "")
                         or "")

        patent_doc = PatentDocument(
            title=data.get("title", ""),
            abstract=data.get("abstract", ""),
            claims=self._parse_claims(data.get("claims", "")),
            description=data.get("description", ""),
            ipc_classifications=[data.get("ipc", "")] if data.get("ipc") else [],
            applicants=[data.get("applicant", "")] if data.get("applicant") else [],
            publication_number=data.get("publication_number", ""),
            application_number=data.get("application_number", ""),
            publication_date=data.get("publication_date", ""),
            application_date=application_date,
            priority_date=priority_date,
            full_text_markdown=data.get("full_text", ""),
        )
        self._apply_manual_dates(patent_doc)
        self._patent_doc = patent_doc

        # 保存到 PDF 旁边的 JSON 文件（含申请日/优先权日，缓存复用）
        if self._pdf_path:
            import json
            pdf_dir = Path(self._pdf_path).parent
            pub = patent_doc.publication_number or "unknown"
            info_path = pdf_dir / f"本申请_{pub}.json"
            if not data.get("application_date"):
                data["application_date"] = patent_doc.application_date
            if not data.get("priority_date"):
                data["priority_date"] = patent_doc.priority_date
            info_path.write_text(json.dumps(data, indent=2,
                ensure_ascii=False, default=str), encoding="utf-8")
            self.log_panel.append_log("INFO",
                f"本申请信息已保存: {info_path}")

        self.log_panel.append_log("SUCCESS",
            f"标题: {patent_doc.title} | "
            f"权利要求: {len(patent_doc.claims)}项 | "
            f"IPC: {', '.join(patent_doc.ipc_classifications)}")
        # 继续生成检索式
        self._run_query_generate(patent_doc)

    @staticmethod
    def _parse_claims(claims_text: str) -> list[str]:
        """将权利要求的纯文本按编号拆分为列表"""
        import re
        if not claims_text:
            return []
        # 按 [权利要求 N] 或 数字. 拆分
        parts = re.split(r'(?:\[权利要求\s*\d+\]|\n(?=\d+[.、．]))', claims_text)
        result = [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]
        return result if result else [claims_text]

    def _run_query_generate(self, patent_doc):
        ai_provider = self.settings.ai_query_provider or self._user_params.get("ai_provider", "deepseek")
        max_queries = self._user_params.get("max_queries", 3)
        self.status_label.setText(f"正在生成 {max_queries} 个检索式 ({ai_provider})...")
        self._current_worker = QueryGenerateWorker(
            patent_doc, self.settings,
            ai_provider=ai_provider, max_queries=max_queries)
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
        max_results = self._user_params.get("max_results",
                        self.settings.patentscope_max_results)
        fetch_detail = self._user_params.get("fetch_detail",
                        self.settings.analysis_max_detail_fetch)
        stop_after = self._user_params.get("stop_after", "full")
        max_queries = len(queries)
        stop_labels = {"abstracts":"搜摘要", "screen":"下载前", "download":"下载", "score":"评分", "full":"全程"}
        self.status_label.setText(
            f"正在 {self._engine_name()} 检索 ({max_queries}检索式 × {max_results}条, "
            f"下载上限{fetch_detail}篇, 断点:{stop_labels.get(stop_after,'?')})...")
        # 共享缓存目录（PDF 同级，多次运行共用）
        cache_dir = None
        if self._pdf_path:
            from src.utils.paths import patent_detail_dir
            cache_dir = patent_detail_dir(Path(self._pdf_path).parent)
        self._current_worker = PatentscopeSearchAndFetchWorker(
            queries, self.settings,
            patent_doc=self._patent_doc,
            max_fetch=fetch_detail,           # 全文下载上限（UI控制）
            output_dir=self._output_dir,
            cache_dir=str(cache_dir) if cache_dir else None,
            stop_after=stop_after,
            include_citations=self._user_params.get("include_citations", True))
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        # 主流程不显示中间摘要，只在 all_searches_done 后显示最终筛选结果
        w.signals.all_searches_done.connect(self._on_all_searches_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    @Slot(bool, str)
    def _on_login_done(self, success, msg):
        if not success:
            self.log_panel.append_log("ERROR", f"登录失败: {msg}")

    @Slot(list)
    def _on_all_searches_done(self, all_enriched):
        """
        全部检索+抓取完成（结果已包含全文）。

        all_enriched: list[list[dict]]，每条检索式的 enriched results。
        enriched result 包含: publication_number, title, full_text,
                              claims, description, abstract 等。
        """
        self._all_raw_results = all_enriched

        # 断点模式：结果已在 Worker 中处理好，直接显示
        stop_after = self._user_params.get("stop_after", "full")
        if stop_after != "full":
            from src.result_collector.deduplicator import Deduplicator
            deduper = Deduplicator(self.settings)
            deduped, removed = deduper.deduplicate(all_enriched)
            self._dedup_results = deduped
            total_raw = sum(len(r) for r in all_enriched)
            self.log_panel.append_log("SUCCESS",
                f"断点结果: 原始 {total_raw} 条 → 去重后 {len(deduped)} 条")
            self.result_panel.show_dedup_results(deduped)
            self.status_label.setText(
                f"断点完成 — {len(deduped)} 篇")
            self.input_panel.set_running_state(False)
            return

        # 去重 + 进入分析
        self._run_dedup_and_analyze(all_enriched)

    def _run_dedup_and_analyze(self, all_enriched):
        self.status_label.setText("正在去重...")
        self.log_panel.append_log("INFO", "开始去重...")

        from src.result_collector.deduplicator import Deduplicator
        deduper = Deduplicator(self.settings)
        deduped, removed = deduper.deduplicate(all_enriched)

        self._dedup_results = deduped

        total_raw = sum(len(r) for r in all_enriched)
        full_text_count = sum(1 for r in deduped if r.get("full_text"))
        self.log_panel.append_log("SUCCESS",
            f"去重完成: 原始 {total_raw} 条 → 去重后 {len(deduped)} 条 "
            f"(移除 {removed} 条)，其中 {full_text_count} 篇已获取全文")
        self.result_panel.show_dedup_results(deduped)

        # 直接进入对比分析
        if self._patent_doc and deduped:
            self.log_panel.append_log("INFO",
                f"开始对比分析: 本申请 vs {len(deduped)} 篇对比文献")
            self._run_analysis(self._patent_doc, deduped)
        else:
            self.log_panel.append_log("WARN", "没有对比文献或专利文档，跳过分析")
            self.log_panel.update_progress(100, "完成（无分析）")
            self.input_panel.set_running_state(False)
            self.status_label.setText("完成")

    def _run_analysis(self, patent_doc, dedup_results):
        ai_provider = self._user_params.get("ai_provider", "deepseek")
        self.status_label.setText("正在进行对比分析...")
        self._current_worker = AnalysisWorker(
            patent_doc, dedup_results, self.settings, ai_provider=ai_provider)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.analysis_done.connect(self._on_analysis_report)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    def _on_analysis_report(self, report):
        """分析完成：显示报告 + 自动保存 + 启动 OA 撰写"""
        self.report_panel.show_report(report)
        self._analysis_report = report

        # 构建对比缓存：{公布号: markdown}，点击专利时秒开（不调AI）
        self._comparison_cache = {}
        for c in (report.comparisons or []):
            pub = c.get("publication_number", "")
            if not pub:
                continue
            # 把 _detailed_comparison() 的 JSON 结果转成丰富的 Markdown
            title = c.get("title", "")
            score = c.get("relevance_score", "?")
            novelty = c.get("novelty_impact", "?")
            inventive = c.get("inventive_step_impact", "?")
            same_features = c.get("key_features_same", [])
            diff_features = c.get("key_features_different", [])
            conclusion = c.get("conclusion", "")

            src = c.get("source_raw", {})
            cand_title = src.get("title", title)
            cand_ipc = src.get("ipc", "")
            cand_applicant = src.get("applicant", "")
            cand_date = src.get("publication_date", "")

            # 新颖性/创造性中文标签
            def impact_label(v):
                m = {"high": "⚠️ 高（可能影响授权）", "moderate": "⚡ 中（需要关注）",
                     "low": "✅ 低（影响有限）"}
                return m.get(str(v).lower(), str(v))

            same_lines = "\n".join(f"- {f}" for f in same_features) if same_features else "- *(AI 未列出)*"
            diff_lines = "\n".join(f"- {f}" for f in diff_features) if diff_features else "- *(AI 未列出)*"

            md = f"""# 对比分析: {pub} vs 本申请

## 1. 基本信息对比

| 项目 | 本申请 | 对比文献 |
|------|--------|----------|
| 标题 | {self._patent_doc.title if self._patent_doc else '?'} | {cand_title} |
| IPC | {', '.join(self._patent_doc.ipc_classifications[:3]) if self._patent_doc else '?'} | {cand_ipc} |
| 申请人 | - | {cand_applicant} |
| 公开日 | - | {cand_date} |

## 2. 相关度评分

**{score}/100** — {impact_label(score)}

## 3. 新颖性影响

{impact_label(novelty)}

## 4. 创造性影响

{impact_label(inventive)}

## 5. 技术特征对比

### 相同特征
{same_lines}

### 不同特征
{diff_lines}

## 6. 综合结论

{conclusion or '*(无)*'}
"""
            self._comparison_cache[pub] = md

        # 持久化对比缓存到磁盘
        cache_path = self._output_dir / "comparison_cache.json"
        try:
            import json as _json
            cache_path.write_text(
                _json.dumps(self._comparison_cache, indent=2, ensure_ascii=False),
                encoding="utf-8")
            self.log_panel.append_log("INFO",
                f"  对比缓存已保存: {cache_path} ({len(self._comparison_cache)} 篇)")
        except Exception as e:
            self.log_panel.append_log("WARN", f"  对比缓存保存失败: {e}")

        # 确保输出目录存在（正常流程 _on_pdf_done 已创建）
        if not self._output_dir:
            # 降级：从 worker 取或自建
            if (self._current_worker and
                    hasattr(self._current_worker, 'output_dir') and
                    self._current_worker.output_dir):
                self._output_dir = self._current_worker.output_dir
            else:
                from datetime import datetime
                from src.utils.paths import normalize_patent_number
                pname = normalize_patent_number(
                    self._patent_doc.publication_number
                    if self._patent_doc and self._patent_doc.publication_number
                    else "unknown"
                )
                ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                self._output_dir = Path.cwd() / "data" / "output" / f"{pname}_{ts}"
            self._output_dir.mkdir(parents=True, exist_ok=True)

        # 保存 Markdown
        md_path = self._output_dir / "04_analysis_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report.markdown_content)
        self.log_panel.append_log("INFO", f"  报告已保存: {md_path}")

        # 保存 HTML
        html_path = self._output_dir / "04_analysis_report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(report.html_content)
        self.log_panel.append_log("INFO", f"  报告已保存: {html_path}")

        # 启动审查意见通知书撰写
        self._run_oa_writing(
            self._patent_doc, report.comparisons, self._dedup_results)

    def _run_oa_writing(self, patent_doc, comparisons, dedup_results,
                        options: dict | None = None):
        """启动审查意见通知书撰写"""
        ai_provider = self._user_params.get("ai_provider", "deepseek")
        self._oa_options = options or {}
        self.status_label.setText("正在撰写审查意见通知书...")
        self.log_panel.append_log("INFO", "=" * 40)
        self.log_panel.append_log("INFO", "阶段5: AI 撰写审查意见通知书...")

        self._current_worker = OAWriterWorker(
            patent_doc, dedup_results, comparisons,
            self.settings, ai_provider=ai_provider, options=self._oa_options)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.analysis_done.connect(self._on_oa_done)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.start()

    def _on_oa_done(self, oa_markdown: str):
        """OA 通知书撰写完成"""
        self.log_panel.append_log("SUCCESS", "审查意见通知书撰写完成!")

        # 清理格式：去掉开头多余的 --- 和空行
        oa_markdown = oa_markdown.strip()
        while oa_markdown.startswith("---"):
            oa_markdown = oa_markdown[3:].strip()

        options = getattr(self, "_oa_options", {}) or {}

        # 保存 OA 通知书（Markdown）
        if options.get("output_md", True):
            oa_path = self._output_dir / "05_审查意见通知书.md"
            with open(oa_path, "w", encoding="utf-8") as f:
                f.write(oa_markdown)
            self.log_panel.append_log("INFO", f"  通知书已保存: {oa_path}")
        else:
            oa_path = self._output_dir / "05_审查意见通知书.md"

        # 保存 HTML
        if options.get("output_html", True):
            final_path = self._output_dir / "05_审查意见通知书.html"
            try:
                import markdown as md_lib
                oa_html = md_lib.markdown(oa_markdown, extensions=["tables", "fenced_code"])
                styled_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>审查意见通知书</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.8; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; }}
h2 {{ border-bottom: 1px solid #999; padding-bottom: 5px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
th {{ background: #f0f0f0; }}
blockquote {{ border-left: 3px solid #ccc; padding-left: 15px; color: #555; }}
</style></head>
<body>
{oa_html}
</body></html>"""
                with open(final_path, "w", encoding="utf-8") as f:
                    f.write(styled_html)
                self.log_panel.append_log("INFO", f"  通知书 HTML: {final_path}")
            except Exception:
                pass

        # 保存 DOCX（按 office_action 版式规范，从零生成）
        if options.get("output_docx", False):
            try:
                from src.analysis.oa_docx import markdown_to_oa_docx
                docx_path = self._output_dir / "07_审查意见通知书.docx"
                markdown_to_oa_docx(oa_markdown, docx_path)
                self.log_panel.append_log("INFO", f"  通知书 DOCX: {docx_path}")
            except Exception as e:
                self.log_panel.append_log("ERROR", f"  DOCX 生成失败: {e}")

        # 在报告面板显示
        self.report_panel.browser.setHtml(oa_markdown)
        self.input_panel.set_running_state(False)
        self.status_label.setText("审查意见通知书撰写完成")

    def _on_oa_write_clicked(self):
        """点击「📝 撰写通知书」→ 打开 OAWriteDialog 选择对比文件。"""
        if not self._patent_doc:
            QMessageBox.warning(self, "提示",
                "请先选择专利申请 PDF 并完成解析，再执行撰写通知书。")
            return

        from src.ui.dialogs import OAWriteDialog
        dlg = OAWriteDialog(
            self._patent_doc, self._dedup_results, self.settings, parent=self)
        dlg.start_oa.connect(self._on_oa_start)
        dlg.exec()

    def _on_oa_start(self, payload: dict):
        """OAWriteDialog 确认 → 启动 OA 撰写。"""
        patent_doc = payload.get("patent_doc")
        dedup_results = payload.get("dedup_results") or self._dedup_results or []
        comparisons = payload.get("comparisons") or []
        options = payload.get("options") or {}

        if not patent_doc:
            QMessageBox.warning(self, "提示", "缺少本申请信息，无法撰写通知书。")
            return
        if not dedup_results:
            QMessageBox.warning(self, "提示", "未选择对比文件，无法撰写通知书。")
            return

        # 输出目录兜底
        if not self._output_dir:
            from datetime import datetime
            base = Path.cwd() / "data" / "output"
            base.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._output_dir = base / f"oa_{ts}"
            self._output_dir.mkdir(parents=True, exist_ok=True)

        self.input_panel.set_running_state(True)
        self._run_oa_writing(patent_doc, comparisons, dedup_results, options)

    # --- 终选评述（从历史最佳中挑最终几篇做详细评述）---

    def _select_snapshots_dialog(self, pdf_dir, pub_num):
        """综评前弹窗：列出所有评分快照供勾选，默认全选。

        Returns:
            选中的快照文件路径列表；无快照返回 []；用户取消返回 None
        """
        from src.analysis.history import list_score_snapshots
        snaps = list_score_snapshots(pdf_dir, pub_num)
        if not snaps:
            return []

        from PySide6.QtWidgets import (
            QDialog, QListWidget, QListWidgetItem,
            QPushButton, QVBoxLayout, QHBoxLayout, QLabel)
        dlg = QDialog(self)
        dlg.setWindowTitle("选择参与综评的评分记录")
        dlg.resize(500, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            f"共 {len(snaps)} 次评分记录，勾选参与综评的（默认全选）："))
        lst = QListWidget()
        for s in snaps:
            created = s.get("created_at") or s.get("name") or "?"
            run_dir = s.get("run_dir", "")
            label = (f"{run_dir} · {created}（{s.get('count', 0)} 篇）"
                     if run_dir else f"{created}（{s.get('count', 0)} 篇）")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, s["path"])
            item.setCheckState(Qt.Checked)
            lst.addItem(item)
        lay.addWidget(lst)

        btn_lay = QHBoxLayout()
        ok_btn = QPushButton("开始综评")
        cancel_btn = QPushButton("取消")
        ok_btn.setDefault(True)
        btn_lay.addWidget(ok_btn)
        btn_lay.addWidget(cancel_btn)
        lay.addLayout(btn_lay)

        selected = []
        def _on_ok():
            for i in range(lst.count()):
                item = lst.item(i)
                if item.checkState() == Qt.Checked:
                    selected.append(item.data(Qt.UserRole))
            dlg.accept()
        ok_btn.clicked.connect(_on_ok)
        cancel_btn.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return selected

    def _on_final_review(self):
        """点击「终选评述」：从历史最佳对比文件中挑最终 N 篇做详细评述"""
        if not self._patent_doc:
            QMessageBox.warning(self, "提示",
                "请先选择专利申请 PDF 并运行「开始分析」积累评分记录，"
                "再执行终选评述。")
            return
        if not self._pdf_path or not Path(self._pdf_path).exists():
            QMessageBox.warning(self, "提示", "请先选择专利申请 PDF 文件")
            return
        if not (self._patent_doc.publication_number or ""):
            QMessageBox.warning(self, "提示", "本申请缺少公布号，无法定位历史记录库")
            return

        ai_provider = self._user_params.get("ai_provider", "deepseek")

        # 综评前：弹窗列出所有评分快照供勾选（默认全选）
        selected = self._select_snapshots_dialog(
            Path(self._pdf_path).parent,
            self._patent_doc.publication_number)
        if selected is None:
            return  # 用户取消
        if not selected:
            QMessageBox.warning(self, "提示",
                "没有评分记录，请先运行「开始分析」完成检索+Claims 广筛。")
            return

        final_n = self.settings.analysis_final_review_n
        self.log_panel.append_log("INFO", "=" * 50)
        self.log_panel.append_log("INFO",
            f"终选评述: 从选中的 {len(selected)} 份评分记录中挑最终 {final_n} 篇做详细评述")

        # 输出目录兜底为 PDF 所在目录（OA 通知书 / 终选评述都存这里）
        if not self._output_dir:
            self._output_dir = Path(self._pdf_path).parent

        self.input_panel.set_running_state(True)
        self.status_label.setText("终选评述...")
        self._current_worker = FinalReviewWorker(
            self._patent_doc, self.settings, self._pdf_path,
            ai_provider=ai_provider, snapshot_files=selected)
        w = self._current_worker
        w.signals.progress.connect(self.log_panel.update_progress)
        w.signals.log.connect(self.log_panel.append_log)
        w.signals.error.connect(self._handle_error)
        w.signals.finished.connect(self._on_worker_finished)
        w.review_markdown.connect(self._on_final_review_markdown)
        w.review_done.connect(self._on_final_review_done)
        w.start()

    def _on_final_review_markdown(self, md: str):
        """终选评述 markdown → 报告面板显示"""
        import json as _json
        if not self._output_dir:
            self._output_dir = Path(self._pdf_path).parent
        try:
            (self._output_dir / "06_终选评述.md").write_text(md, encoding="utf-8")
        except Exception:
            pass

        class _FakeReport:
            pass
        report = _FakeReport()
        report.markdown_content = md
        report.html_content = ""
        report.comparisons = []
        self.report_panel.show_report(report)

    def _on_final_review_done(self, comparisons: list):
        """终选评述完成 → 重置运行态 + 更新对比缓存 + 续接 OA"""
        self.input_panel.set_running_state(False)
        self.status_label.setText("终选评述完成")

        # 更新对比缓存（点击专利秒开，不调 AI）
        self._comparison_cache = {}
        for c in comparisons:
            pub = c.get("publication_number", "")
            if not pub:
                continue
            self._comparison_cache[pub] = self._render_comparison_md(pub, c)

        # 续接 OA 撰写（复用现有链路；source_raw 含全文供 OAWriter 取对比文件）
        if comparisons and self._patent_doc:
            self.log_panel.append_log("INFO",
                f"终选评述 {len(comparisons)} 篇，开始撰写审查意见通知书...")
            dedup_results = [c.get("source_raw", {}) or {} for c in comparisons]
            self._run_oa_writing(
                self._patent_doc, comparisons, dedup_results)
        else:
            self.log_panel.append_log("WARN", "终选评述无结果，跳过 OA")

    @staticmethod
    def _render_comparison_md(pub: str, c: dict) -> str:
        """渲染单篇终选评述的 Markdown（供点击专利时秒开）"""
        src = c.get("source_raw", {}) or {}
        title = c.get("title", "") or src.get("title", "")
        score = c.get("relevance_score", "?")
        novelty = c.get("novelty_impact", "?")
        inventive = c.get("inventive_step_impact", "?")
        same = c.get("key_features_same", []) or []
        diff = c.get("key_features_different", []) or []
        conclusion = c.get("conclusion", "")

        def impact_label(v):
            m = {"high": "⚠️ 高（可能影响授权）", "moderate": "⚡ 中（需要关注）",
                 "low": "✅ 低（影响有限）"}
            return m.get(str(v).lower(), str(v))

        same_lines = "\n".join(f"- {f}" for f in same) if same else "- *(AI 未列出)*"
        diff_lines = "\n".join(f"- {f}" for f in diff) if diff else "- *(AI 未列出)*"
        return f"""# 终选评述: {pub}

## 基本信息
- **标题**: {title}
- **相关度**: {score}/100

## 新颖性影响
{impact_label(novelty)}

## 创造性影响
{impact_label(inventive)}

## 相同技术特征
{same_lines}

## 不同技术特征
{diff_lines}

## 综合结论
{conclusion or '*(无)*'}
"""

    @Slot(dict)
    def _on_patent_clicked(self, patent: dict):
        """点击专利 → 优先从缓存加载完整详情（含权利要求/说明书）"""
        pub = patent.get("publication_number", "?")
        # 尝试从缓存加载完整详情
        full = self._load_cached_detail(patent)
        if full:
            patent = full
        self.report_panel.show_patent_detail(patent)
        if self._comparison_cache and pub in self._comparison_cache:
            self.report_panel.show_single_comparison(
                pub, self._comparison_cache[pub])

    def _load_cached_detail(self, patent: dict) -> dict | None:
        """从缓存文件加载完整专利详情"""
        import json as _json
        from src.web_automation.patentscope_scraper import is_cached_patent_valid, _safe_filename
        pub = patent.get("publication_number", "")
        doc_id = patent.get("doc_id", "")
        if not self._pdf_path:
            return None
        from src.utils.paths import patent_detail_dir
        cache_dir = patent_detail_dir(Path(self._pdf_path).parent)
        for key in (pub, doc_id):
            if not key:
                continue
            fpath = cache_dir / f"{_safe_filename(key)}.json"
            if fpath.exists():
                try:
                    data = _json.loads(fpath.read_text(encoding="utf-8"))
                    if is_cached_patent_valid(data):
                        return data
                except Exception:
                    pass
        return None

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
        # 断开浏览器连接
        import asyncio as _asyncio
        from src.web_automation.browser_manager import BrowserManager
        try:
            _asyncio.get_event_loop().run_until_complete(BrowserManager.shutdown())
        except Exception:
            pass
        event.accept()
