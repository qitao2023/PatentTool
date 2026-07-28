"""
输入设置面板 - 文件路径编辑框、参数设置、控制按钮、历史运行选择、测试工具
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QRadioButton, QGroupBox, QFileDialog,
    QButtonGroup, QListWidget, QListWidgetItem, QAbstractItemView,
    QFrame, QComboBox, QCheckBox,
)
from PySide6.QtCore import Signal, Slot


class InputPanel(QWidget):
    """顶部输入设置区域"""

    # 信号
    start_clicked = Signal()
    stop_clicked = Signal()
    reset_clicked = Signal()
    file_selected = Signal(str)
    search_abstract_clicked = Signal()  # 仅搜索摘要
    settings_clicked = Signal()
    test_clicked = Signal(str, int)             # 测试: 搜索+抓详情
    test_abstract_clicked = Signal(str, int)    # 测试: 仅搜索摘要
    lookup_patent = Signal(str)                 # 公布号直查
    open_existing = Signal(str)                 # 打开已有结果

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ai_provider = "deepseek"
        self._runs_data: list[dict] = []
        self._test_visible = False
        self._history_file = Path.cwd() / "data" / "lookup_history.json"
        self._lookup_history: list[str] = self._load_history()
        self._setup_ui()
        self._set_default_test_path()

    def _set_default_test_path(self):
        test_path = r"E:\01-claudecode\00-patent\01-20260724\本申请.PDF"
        if Path(test_path).exists():
            self.path_edit.setText(test_path)
            self.path_edit.home(False)

    # ── UI 构建 ──────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 4)
        layout.setSpacing(4)

        # ── 文件路径行 ──────────────────────────────────────────────
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("专利PDF路径:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("请选择或输入专利申请文件PDF路径...")
        self.path_edit.setMinimumWidth(400)
        file_row.addWidget(self.path_edit, 1)

        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(self.browse_btn)
        layout.addLayout(file_row)

        # ── 历史运行选择 ────────────────────────────────────────────
        self.runs_group = QGroupBox("📂 历史运行")
        self.runs_group.setVisible(False)
        runs_layout = QVBoxLayout(self.runs_group)
        runs_layout.setContentsMargins(8, 4, 8, 4)
        runs_layout.setSpacing(2)

        self.runs_list = QListWidget()
        self.runs_list.setMaximumHeight(100)
        self.runs_list.setSelectionMode(QAbstractItemView.SingleSelection)
        runs_layout.addWidget(self.runs_list)

        runs_btn_row = QHBoxLayout()
        self.open_existing_btn = QPushButton("📂 打开选中结果")
        self.open_existing_btn.clicked.connect(self._on_open_existing_clicked)
        runs_btn_row.addWidget(self.open_existing_btn)
        self.new_analysis_btn = QPushButton("🔄 开始新分析")
        self.new_analysis_btn.clicked.connect(self.start_clicked.emit)
        runs_btn_row.addWidget(self.new_analysis_btn)
        runs_btn_row.addStretch(1)
        runs_layout.addLayout(runs_btn_row)

        layout.addWidget(self.runs_group)

        # ── 参数行 ──────────────────────────────────────────────────
        param_row = QHBoxLayout()

        param_row.addWidget(QLabel("最多检索式数:"))
        self.max_queries_spin = QSpinBox()
        self.max_queries_spin.setRange(1, 20)
        self.max_queries_spin.setValue(10)
        self.max_queries_spin.setSuffix(" 个")
        param_row.addWidget(self.max_queries_spin)
        param_row.addSpacing(20)

        param_row.addWidget(QLabel("每式结果数:"))
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 200)
        self.max_results_spin.setValue(200)
        self.max_results_spin.setSuffix(" 条/检索式")
        param_row.addWidget(self.max_results_spin)
        param_row.addSpacing(20)

        param_row.addWidget(QLabel("全文下载上限:"))
        self.fetch_detail_spin = QSpinBox()
        self.fetch_detail_spin.setRange(1, 1000)
        self.fetch_detail_spin.setValue(200)
        self.fetch_detail_spin.setSuffix(" 篇")
        param_row.addWidget(self.fetch_detail_spin)
        param_row.addStretch(1)

        param_row.addSpacing(20)
        self.include_citations_cb = QCheckBox("从说明书提取引用专利")
        self.include_citations_cb.setChecked(True)
        self.include_citations_cb.setToolTip(
            "自动提取说明书中引用的专利号，一并下载加入对比文件")
        param_row.addWidget(self.include_citations_cb)

        self.ai_label = QLabel("DeepSeek")
        self.ai_label.setObjectName("aiProviderLabel")
        param_row.addWidget(self.ai_label)

        layout.addLayout(param_row)

        # ── 控制按钮行 ──────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始分析")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.start_clicked.emit)
        btn_row.addWidget(self.start_btn)

        self.search_abstract_btn = QPushButton("🔍 检索摘要")
        self.search_abstract_btn.setToolTip(
            "仅执行检索并生成摘要列表，不下载全文，用于快速验证对比文件")
        self.search_abstract_btn.setMinimumHeight(40)
        self.search_abstract_btn.clicked.connect(self.search_abstract_clicked.emit)
        btn_row.addWidget(self.search_abstract_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        btn_row.addWidget(self.stop_btn)

        self.reset_btn = QPushButton("↻ 重置")
        self.reset_btn.clicked.connect(self.reset_clicked.emit)
        btn_row.addWidget(self.reset_btn)

        self.test_toggle_btn = QPushButton("🧪 测试")
        self.test_toggle_btn.setCheckable(True)
        self.test_toggle_btn.setObjectName("testToggleBtn")
        self.test_toggle_btn.setToolTip("显示/隐藏测试工具")
        self.test_toggle_btn.clicked.connect(self._toggle_test)
        btn_row.addWidget(self.test_toggle_btn)

        btn_row.addStretch(1)

        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setMinimumHeight(36)
        self.settings_btn.setToolTip("配置AI引擎、API Key、模型等")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        btn_row.addWidget(self.settings_btn)

        layout.addLayout(btn_row)

        # ── 测试工具（可折叠） ──────────────────────────────────────
        self.test_frame = QFrame()
        self.test_frame.setObjectName("testFrame")
        self.test_frame.setVisible(False)
        test_outer = QVBoxLayout(self.test_frame)
        test_outer.setContentsMargins(8, 4, 8, 4)
        test_outer.setSpacing(4)

        # 行1: 检索式测试
        test_row1 = QHBoxLayout()
        test_row1.setSpacing(8)
        test_row1.addWidget(QLabel("检索式:"))
        self.test_query_edit = QLineEdit()
        self.test_query_edit.setPlaceholderText("输入 PATENTSCOPE 检索式...")
        self.test_query_edit.setText("掉电")
        self.test_query_edit.setMinimumWidth(200)
        test_row1.addWidget(self.test_query_edit, 1)
        test_row1.addWidget(QLabel("数量:"))
        self.test_count_spin = QSpinBox()
        self.test_count_spin.setRange(1, 10)
        self.test_count_spin.setValue(5)
        self.test_count_spin.setSuffix(" 条")
        self.test_count_spin.setMaximumWidth(80)
        test_row1.addWidget(self.test_count_spin)
        self.test_abstract_btn = QPushButton("🔍 测试摘要")
        self.test_abstract_btn.setToolTip("仅搜索摘要（快，验证检索式）")
        self.test_abstract_btn.clicked.connect(self._on_test_abstract_clicked)
        test_row1.addWidget(self.test_abstract_btn)
        self.test_btn = QPushButton("📄 测试详情")
        self.test_btn.setToolTip("搜索摘要 + 抓全文详情")
        self.test_btn.clicked.connect(self._on_test_clicked)
        test_row1.addWidget(self.test_btn)
        test_outer.addLayout(test_row1)

        # 行2: 公布号直查
        test_row2 = QHBoxLayout()
        test_row2.setSpacing(8)
        test_row2.addWidget(QLabel("公布号:"))
        self.lookup_combo = QComboBox()
        self.lookup_combo.setEditable(True)
        self.lookup_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lookup_combo.setMinimumWidth(200)
        self.lookup_combo.lineEdit().setPlaceholderText("输入公布号，如 WO2019006821...")
        # 加载历史
        for h in self._lookup_history:
            self.lookup_combo.addItem(h)
        test_row2.addWidget(self.lookup_combo, 1)
        self.lookup_btn = QPushButton("🔎 查看专利")
        self.lookup_btn.setToolTip("直接抓取专利详情并显示")
        self.lookup_btn.clicked.connect(self._on_lookup_clicked)
        test_row2.addWidget(self.lookup_btn)

        test_row2.addSpacing(20)
        self.debug_search_only_cb = QCheckBox("仅搜索（调试断点）")
        self.debug_search_only_cb.setToolTip(
            "勾选后全部检索式搜索完就停止，不下载全文、不AI评分")
        test_row2.addWidget(self.debug_search_only_cb)
        self.force_refresh_cb = QCheckBox("强制重新获取本申请")
        self.force_refresh_cb.setToolTip(
            "勾选后忽略本地缓存，重新从 PATENTSCOPE 获取本申请完整信息")
        test_row2.addWidget(self.force_refresh_cb)
        test_outer.addLayout(test_row2)

        layout.addWidget(self.test_frame)

        # 测试按钮样式
        self.setStyleSheet("""
            QPushButton#testToggleBtn:checked {
                background: #e8f0fe;
                border: 1px solid #0078d4;
            }
            QFrame#testFrame {
                background: #f8f9fa;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
            }
        """)

    # ── 测试面板 ──────────────────────────────────────────────────────

    def _toggle_test(self):
        self._test_visible = not self._test_visible
        self.test_frame.setVisible(self._test_visible)
        self.test_toggle_btn.setChecked(self._test_visible)

    def _on_lookup_clicked(self):
        doc_id = self.lookup_combo.currentText().strip()
        if doc_id:
            self._add_history(doc_id)
            self.lookup_patent.emit(doc_id)

    # ── 查询历史 ──────────────────────────────────────────────────

    def _load_history(self) -> list[str]:
        try:
            if self._history_file.exists():
                import json
                data = json.loads(self._history_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[:20]  # 最多保留20条
        except Exception:
            pass
        return []

    def _save_history(self):
        try:
            self._history_file.parent.mkdir(parents=True, exist_ok=True)
            self._history_file.write_text(
                __import__('json').dumps(self._lookup_history, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    def _add_history(self, doc_id: str):
        doc_id = doc_id.strip()
        if not doc_id:
            return
        # 去重：移除旧的，插入到最前
        if doc_id in self._lookup_history:
            self._lookup_history.remove(doc_id)
        self._lookup_history.insert(0, doc_id)
        self._lookup_history = self._lookup_history[:20]
        # 更新下拉框
        self.lookup_combo.clear()
        for h in self._lookup_history:
            self.lookup_combo.addItem(h)
        self.lookup_combo.setCurrentIndex(0)
        self._save_history()

    def _on_test_abstract_clicked(self):
        q = self.test_query_edit.text().strip()
        if q:
            self.test_abstract_clicked.emit(q, self.test_count_spin.value())

    def _on_test_clicked(self):
        q = self.test_query_edit.text().strip()
        if q:
            self.test_clicked.emit(q, self.test_count_spin.value())

    # ── 公开方法 ──────────────────────────────────────────────────────

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择专利PDF文件", "",
            "PDF文件 (*.pdf);;所有文件 (*.*)"
        )
        if path:
            self.path_edit.setText(path)
            self.file_selected.emit(path)

    def show_runs(self, runs: list):
        self._runs_data = runs
        self.runs_list.clear()
        if not runs:
            self.runs_group.setVisible(False)
            return

        self.runs_group.setVisible(True)
        stage_labels = {
            "search": "检索", "screen": "筛选", "detail": "详情",
            "analysis": "分析", "oa": "OA",
        }
        for i, r in enumerate(runs):
            badges = []
            for key, label in stage_labels.items():
                mark = "✓" if r.stages.get(key) else "✗"
                badges.append(f"[{label}{mark}]")
            status = " ".join(badges)
            item = QListWidgetItem(f"{r.folder_name}  {status}")
            item.setData(1, r.path)
            self.runs_list.addItem(item)
            if i == 0:
                self.runs_list.setCurrentItem(item)

    def _on_open_existing_clicked(self):
        item = self.runs_list.currentItem()
        if item:
            self.open_existing.emit(str(item.data(1)))

    def get_test_query(self) -> str:
        return self.test_query_edit.text().strip()

    def get_pdf_path(self) -> str:
        return self.path_edit.text().strip()

    def get_params(self) -> dict:
        return {
            "max_queries": self.max_queries_spin.value(),
            "max_results": self.max_results_spin.value(),
            "fetch_detail": self.fetch_detail_spin.value(),
            "ai_provider": self._ai_provider,
            "debug_search_only": self.debug_search_only_cb.isChecked(),
            "force_refresh": self.force_refresh_cb.isChecked(),
            "include_citations": self.include_citations_cb.isChecked(),
        }

    def set_ai_provider(self, provider: str):
        self._ai_provider = provider
        provider_display = {"deepseek": "DeepSeek", "kimi": "Kimi"}
        self.ai_label.setText(provider_display.get(provider, provider))

    def set_running_state(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.search_abstract_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.browse_btn.setEnabled(not running)
        self.max_queries_spin.setEnabled(not running)
        self.max_results_spin.setEnabled(not running)
        self.fetch_detail_spin.setEnabled(not running)
        self.test_abstract_btn.setEnabled(not running)
        self.test_btn.setEnabled(not running)
        self.lookup_btn.setEnabled(not running)
        self.force_refresh_cb.setEnabled(not running)
        self.include_citations_cb.setEnabled(not running)

    def reset(self):
        self.path_edit.clear()
        self.max_queries_spin.setValue(1)
        self.set_running_state(False)
        self.runs_group.setVisible(False)
        self.runs_list.clear()
        self.test_frame.setVisible(False)
        self._test_visible = False
        self.test_toggle_btn.setChecked(False)
        self.force_refresh_cb.setChecked(False)
