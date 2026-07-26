"""
对话框 - 设置、关于等弹出窗口
"""
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QSlider, QTabWidget, QWidget, QMessageBox,
    QGroupBox,
)
from PySide6.QtCore import Qt, Signal

from src.utils.config import Settings
from src.ai_client import PROVIDER_CONFIG, get_provider_models


class SettingsDialog(QDialog):
    """设置对话框 - AI引擎、API Key等全局配置"""

    # 保存时发射信号，带 (provider, model, temperature)
    settings_saved = Signal(str, str, float)

    # 提供商显示名 -> 内部名 映射
    PROVIDER_NAMES = {
        "DeepSeek": "deepseek",
        "Kimi (月之暗面)": "kimi",
    }
    PROVIDER_KEYS = {v: k for k, v in PROVIDER_NAMES.items()}

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._current_provider = settings.ai_provider
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # --- AI 设置 Tab ---
        ai_group = QGroupBox("AI 引擎设置")
        ai_layout = QFormLayout(ai_group)

        # 提供商
        provider_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        for display_name, internal_name in self.PROVIDER_NAMES.items():
            self.provider_combo.addItem(display_name, internal_name)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo)
        provider_row.addStretch(1)
        ai_layout.addRow("AI 提供商:", provider_row)

        # 模型选择
        self.model_combo = QComboBox()
        ai_layout.addRow("模型:", self.model_combo)

        # API Key
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("输入 API Key...")
        api_key_row = QHBoxLayout()
        api_key_row.addWidget(self.api_key_input, 1)
        self.show_key_btn = QPushButton("显示")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        api_key_row.addWidget(self.show_key_btn)
        ai_layout.addRow("API Key:", api_key_row)

        # 温度
        temp_row = QHBoxLayout()
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(40)  # 默认 0.4
        self.temp_label = QLabel("0.40")
        self.temp_slider.valueChanged.connect(
            lambda v: self.temp_label.setText(f"{v/100:.2f}")
        )
        temp_row.addWidget(self.temp_slider, 1)
        temp_row.addWidget(self.temp_label)
        ai_layout.addRow("温度 (Temperature):", temp_row)

        layout.addWidget(ai_group)

        # --- 提示 ---
        hint = QLabel(
            "💡 保存设置时，API Key 和模型选择将写入 config/.env 文件。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch(1)

        # --- 按钮 ---
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load_settings(self):
        """加载当前配置到界面"""
        provider = self._current_provider
        idx = self.provider_combo.findData(provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        # 确保模型列表已填充（setCurrentIndex 为0且当前已在0时不触发信号）
        self._populate_models()

        # 从环境变量加载 API Key（masked）
        cfg = PROVIDER_CONFIG.get(provider, {})
        env_var = cfg.get("api_key_env", "")
        api_key = os.getenv(env_var, "")
        if api_key:
            self.api_key_input.setText(api_key)

        # 温度
        temp = self.settings.ai_temperature
        self.temp_slider.setValue(int(temp * 100))

    def _populate_models(self):
        """根据当前提供商填充模型下拉框"""
        provider = self._current_provider
        models = get_provider_models(provider)
        self.model_combo.clear()
        current_model = self.settings.ai_model
        selected_idx = 0
        for i, (model_name, display_name) in enumerate(models.items()):
            self.model_combo.addItem(f"{model_name} - {display_name}", model_name)
            if model_name == current_model:
                selected_idx = i
        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(selected_idx)

    def _on_provider_changed(self, idx):
        """提供商改变时更新模型列表"""
        self._current_provider = self.provider_combo.currentData()
        self._populate_models()

        # 更新 API Key 输入框
        cfg = PROVIDER_CONFIG.get(self._current_provider, {})
        env_var = cfg.get("api_key_env", "")
        api_key = os.getenv(env_var, "")
        self.api_key_input.setText(api_key)
        self.api_key_input.setPlaceholderText(f"在 .env 中设置 {env_var}=...")

    def _toggle_key_visibility(self, visible: bool):
        """切换 API Key 显隐"""
        self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.show_key_btn.setText("隐藏" if visible else "显示")

    def _on_save(self):
        """保存设置 - 写入 .env 文件"""
        provider = self.provider_combo.currentData()
        model = self.model_combo.currentData()
        temperature = self.temp_slider.value() / 100.0
        api_key = self.api_key_input.text().strip()

        # 写入 .env 文件
        try:
            env_path = self.settings.config_dir / ".env"
            if env_path.exists():
                content = env_path.read_text(encoding="utf-8")
            else:
                content = ""

            cfg = PROVIDER_CONFIG.get(provider, {})
            env_var = cfg.get("api_key_env", "")

            if api_key and env_var:
                # 更新或添加 API Key
                lines = content.splitlines()
                found = False
                new_lines = []
                for line in lines:
                    if line.startswith(f"{env_var}="):
                        new_lines.append(f"{env_var}={api_key}")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"{env_var}={api_key}")
                content = "\n".join(new_lines)

                # 确保没有空行在开头
                content = content.strip() + "\n"
                env_path.write_text(content, encoding="utf-8")

                # 同时更新当前进程环境变量
                os.environ[env_var] = api_key

        except Exception as e:
            QMessageBox.warning(self, "写入 .env 失败",
                f"无法写入配置文件:\n{e}\n\n请手动编辑 config/.env 文件。")

        # 更新 settings.yaml 中的 provider 和 model
        try:
            yaml_path = self.settings.config_dir / "settings.yaml"
            if yaml_path.exists():
                content = yaml_path.read_text(encoding="utf-8")
                # 更新 provider
                import re
                content = re.sub(
                    r'provider:\s*"[^"]*"',
                    f'provider: "{provider}"',
                    content
                )
                # 更新对应模型的默认值
                if provider == "kimi":
                    content = re.sub(
                        r'kimi_model:\s*"[^"]*"',
                        f'kimi_model: "{model}"',
                        content
                    )
                else:
                    content = re.sub(
                        r'deepseek_model:\s*"[^"]*"',
                        f'deepseek_model: "{model}"',
                        content
                    )
                # 更新温度
                content = re.sub(
                    r'temperature:\s*\d+\.?\d*',
                    f'temperature: {temperature}',
                    content
                )
                yaml_path.write_text(content, encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "写入配置失败",
                f"无法更新设置文件:\n{e}")

        self.settings_saved.emit(provider, model, temperature)
        self.accept()
        QMessageBox.information(self, "设置已保存",
            f"AI 引擎: {self.provider_combo.currentText()}\n"
            f"模型: {model}\n"
            f"温度: {temperature:.2f}\n\n"
            "设置已写入 config/.env 和 config/settings.yaml，持续生效。")
