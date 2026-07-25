"""
输入设置面板 - 文件路径编辑框、参数设置、控制按钮
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QRadioButton, QGroupBox, QFileDialog,
    QButtonGroup,
)
from PySide6.QtCore import Signal, Slot


class InputPanel(QWidget):
    """顶部输入设置区域"""

    # 信号
    start_clicked = Signal()
    stop_clicked = Signal()
    reset_clicked = Signal()
    file_selected = Signal(str)
    settings_clicked = Signal()
    test_clicked = Signal()  # 测试连接

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ai_provider = "deepseek"
        self._setup_ui()
        self._set_default_test_path()

    def _set_default_test_path(self):
        """设置测试用的默认PDF路径"""
        test_path = r"E:\01-claudecode\00-patent\01-20260724\本申请.PDF"
        from pathlib import Path
        if Path(test_path).exists():
            self.path_edit.setText(test_path)
            self.path_edit.home(False)  # 光标移到开头

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)

        # --- 文件路径行 ---
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

        # --- 参数行 ---
        param_row = QHBoxLayout()

        # 最多检索式数
        param_row.addWidget(QLabel("最多检索式数:"))
        self.max_queries_spin = QSpinBox()
        self.max_queries_spin.setRange(1, 20)
        self.max_queries_spin.setValue(1)
        param_row.addWidget(self.max_queries_spin)

        param_row.addSpacing(20)

        # 每式结果数
        param_row.addWidget(QLabel("每式结果数:"))
        self.max_results_spin = QSpinBox()
        self.max_results_spin.setRange(10, 100)
        self.max_results_spin.setValue(50)
        self.max_results_spin.setSuffix(" 条")
        param_row.addWidget(self.max_results_spin)

        param_row.addSpacing(20)

        # 登录方式
        param_row.addWidget(QLabel("登录方式:"))
        self.login_group = QButtonGroup(self)
        self.manual_radio = QRadioButton("手动登录(推荐)")
        self.manual_radio.setChecked(True)
        self.auto_radio = QRadioButton("自动登录")
        self.login_group.addButton(self.manual_radio)
        self.login_group.addButton(self.auto_radio)
        param_row.addWidget(self.manual_radio)
        param_row.addWidget(self.auto_radio)

        param_row.addStretch(1)

        # AI引擎标签
        self.ai_label = QLabel("DeepSeek")
        self.ai_label.setStyleSheet("color: #656d76; font-size: 12px;")
        param_row.addWidget(self.ai_label)

        layout.addLayout(param_row)

        # --- 控制按钮行 ---
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始分析")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078D4;
                color: white;
                font-weight: bold;
                padding: 6px 24px;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: #106EBE; }
            QPushButton:disabled { background-color: #CCCCCC; }
        """)
        self.start_btn.clicked.connect(self.start_clicked.emit)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        btn_row.addWidget(self.stop_btn)

        self.test_btn = QPushButton("🔍 测试连接")
        self.test_btn.setToolTip("测试HimmPat登录、页面跳转、元素定位")
        self.test_btn.clicked.connect(self.test_clicked.emit)
        btn_row.addWidget(self.test_btn)

        self.reset_btn = QPushButton("↻ 重置")
        self.reset_btn.clicked.connect(self.reset_clicked.emit)
        btn_row.addWidget(self.reset_btn)

        btn_row.addStretch(1)

        # 设置按钮（显眼位置）
        self.settings_btn = QPushButton("⚙ 设置")
        self.settings_btn.setMinimumHeight(32)
        self.settings_btn.setToolTip("配置AI引擎、API Key、模型等")
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        btn_row.addWidget(self.settings_btn)

        layout.addLayout(btn_row)

    def _on_browse(self):
        """浏览文件对话框"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择专利PDF文件", "",
            "PDF文件 (*.pdf);;所有文件 (*.*)"
        )
        if path:
            self.path_edit.setText(path)
            self.file_selected.emit(path)

    def get_pdf_path(self) -> str:
        return self.path_edit.text().strip()

    def get_params(self) -> dict:
        return {
            "max_queries": self.max_queries_spin.value(),
            "max_results": self.max_results_spin.value(),
            "login_mode": "manual" if self.manual_radio.isChecked() else "auto",
            "ai_provider": self._ai_provider,
        }

    def set_ai_provider(self, provider: str):
        """由父窗口在设置保存后调用"""
        self._ai_provider = provider
        provider_display = {"deepseek": "DeepSeek", "kimi": "Kimi"}
        display = provider_display.get(provider, provider)
        self.ai_label.setText(display)

    def set_running_state(self, running: bool):
        """切换运行/空闲状态"""
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.browse_btn.setEnabled(not running)
        self.max_queries_spin.setEnabled(not running)
        self.max_results_spin.setEnabled(not running)
        self.manual_radio.setEnabled(not running)
        self.auto_radio.setEnabled(not running)
        # 设置按钮在运行中也可用，方便随时调整

    def reset(self):
        self.path_edit.clear()
        self.max_queries_spin.setValue(1)
        self.manual_radio.setChecked(True)
        self.set_running_state(False)
