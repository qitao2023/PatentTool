"""
输入设置面板 - 文件路径编辑框、参数设置、控制按钮、历史运行选择、测试工具
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QGroupBox, QFileDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
)
from PySide6.QtCore import Signal, Slot


class InputPanel(QWidget):
    """顶部输入设置区域"""

    # 信号
    start_clicked = Signal()
    stop_clicked = Signal()
    reset_clicked = Signal()
    file_selected = Signal(str)
    test_clicked = Signal()                      # 打开测试工具对话框
    settings_clicked = Signal()
    test_clicked = Signal()                     # 打开测试工具对话框
    open_existing = Signal(str)                 # 打开已有结果

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ai_provider = "deepseek"
        self._runs_data: list[dict] = []
        self._setup_ui()
        self._set_default_test_path()

    def _set_default_test_path(self):
        test_path = r"E:\01-claudecode\00-patent\03-20260727\本申请.PDF"
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
        self.max_queries_spin.setRange(1, 50)
        self.max_queries_spin.setValue(20)
        self.max_queries_spin.setSuffix(" 个")
        param_row.addWidget(self.max_queries_spin)
        param_row.addSpacing(20)

        param_row.addWidget(QLabel("每式结果数:"))
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(1, 100)
        self.max_results_spin.setValue(100)
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

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        btn_row.addWidget(self.stop_btn)

        self.reset_btn = QPushButton("↻ 重置")
        self.reset_btn.clicked.connect(self.reset_clicked.emit)
        btn_row.addWidget(self.reset_btn)

        self.test_btn = QPushButton("🧪 测试")
        self.test_btn.setToolTip("打开测试工具（检索式测试、公布号直查）")
        self.test_btn.clicked.connect(self.test_clicked.emit)
        btn_row.addWidget(self.test_btn)

        btn_row.addStretch(1)

        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setMinimumHeight(36)
        self.settings_btn.setToolTip("配置AI引擎、检索参数等")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        btn_row.addWidget(self.settings_btn)

        layout.addLayout(btn_row)

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

    def get_pdf_path(self) -> str:
        return self.path_edit.text().strip()

    def get_params(self) -> dict:
        return {
            "max_queries": self.max_queries_spin.value(),
            "max_results": self.max_results_spin.value(),
            "fetch_detail": self.fetch_detail_spin.value(),
            "ai_provider": self._ai_provider,
        }

    def set_ai_provider(self, provider: str):
        self._ai_provider = provider
        provider_display = {"deepseek": "DeepSeek", "kimi": "Kimi"}
        self.ai_label.setText(provider_display.get(provider, provider))

    def set_running_state(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.browse_btn.setEnabled(not running)
        self.max_queries_spin.setEnabled(not running)
        self.max_results_spin.setEnabled(not running)
        self.fetch_detail_spin.setEnabled(not running)
        self.test_btn.setEnabled(not running)

    def reset(self):
        self.path_edit.clear()
        self.max_queries_spin.setValue(1)
        self.set_running_state(False)
        self.runs_group.setVisible(False)
        self.runs_list.clear()
