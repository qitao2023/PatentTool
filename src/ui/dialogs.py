"""
对话框 - 设置、关于等弹出窗口
"""
import json
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QSlider, QTabWidget, QWidget, QMessageBox,
    QGroupBox, QPlainTextEdit, QSpinBox, QCheckBox,
)
from PySide6.QtCore import Qt, Signal

from src.utils.config import Settings
from src.ai_client import PROVIDER_CONFIG, get_provider_models


class SettingsDialog(QDialog):
    """设置对话框 - Tab 页: 大模型设置 + 检索参数"""

    # 保存时发射信号，带 (provider, model, temperature, params_dict)
    settings_saved = Signal(str, str, float, dict)

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
        self.setMinimumSize(540, 500)
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.tab_widget = QTabWidget()

        # ================================================================
        # Tab 1: 大模型设置
        # ================================================================
        tab_ai = QWidget()
        ai_layout = QVBoxLayout(tab_ai)

        # --- AI 全局设置 ---
        ai_group = QGroupBox("AI 引擎设置")
        ai_form = QFormLayout(ai_group)

        provider_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        for display_name, internal_name in self.PROVIDER_NAMES.items():
            self.provider_combo.addItem(display_name, internal_name)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo)
        provider_row.addStretch(1)
        ai_form.addRow("AI 提供商:", provider_row)

        self.model_combo = QComboBox()
        ai_form.addRow("默认模型:", self.model_combo)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("输入 API Key...")
        api_key_row = QHBoxLayout()
        api_key_row.addWidget(self.api_key_input, 1)
        self.show_key_btn = QPushButton("显示")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        api_key_row.addWidget(self.show_key_btn)
        ai_form.addRow("API Key:", api_key_row)

        temp_row = QHBoxLayout()
        self.temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.temp_slider.setRange(0, 100)
        self.temp_slider.setValue(40)
        self.temp_label = QLabel("0.40")
        self.temp_slider.valueChanged.connect(
            lambda v: self.temp_label.setText(f"{v/100:.2f}")
        )
        temp_row.addWidget(self.temp_slider, 1)
        temp_row.addWidget(self.temp_label)
        ai_form.addRow("温度 (Temperature):", temp_row)

        ai_layout.addWidget(ai_group)

        # --- 各阶段专用模型 ---
        stage_group = QGroupBox("各阶段专用模型（为空则用默认模型）")
        stage_form = QFormLayout(stage_group)

        self.query_model_combo = QComboBox()
        self.query_model_combo.setToolTip("生成 PATENTSCOPE 检索式时使用的模型")
        stage_form.addRow("检索式生成:", self.query_model_combo)

        self.screen_model_combo = QComboBox()
        self.screen_model_combo.setToolTip("AI 筛选/评分对比文件时使用的模型")
        stage_form.addRow("筛选评分:", self.screen_model_combo)

        self.analysis_model_combo = QComboBox()
        self.analysis_model_combo.setToolTip("逐篇对比分析时使用的模型")
        stage_form.addRow("对比分析:", self.analysis_model_combo)

        ai_layout.addWidget(stage_group)
        ai_layout.addStretch(1)
        self.tab_widget.addTab(tab_ai, "🤖 大模型设置")

        # ================================================================
        # Tab 2: 参数设置
        # ================================================================
        tab_params = QWidget()
        params_layout = QVBoxLayout(tab_params)

        search_group = QGroupBox("检索参数")
        search_form = QFormLayout(search_group)

        self.include_citations_cb = QCheckBox("从说明书中提取引用专利号")
        self.include_citations_cb.setChecked(True)
        self.include_citations_cb.setToolTip(
            "自动提取说明书中引用的专利号，一并下载加入对比文件")
        search_form.addRow(self.include_citations_cb)

        self.force_refresh_cb = QCheckBox("强制重新获取本申请")
        self.force_refresh_cb.setToolTip(
            "勾选后忽略本地缓存，重新从 PATENTSCOPE 获取本申请完整信息")
        search_form.addRow(self.force_refresh_cb)

        self.debug_search_only_cb = QCheckBox("仅搜索摘要（不下载全文、不AI评分）")
        self.debug_search_only_cb.setToolTip(
            "勾选后全部检索式搜索完就停止，仅生成摘要列表")
        search_form.addRow(self.debug_search_only_cb)

        params_layout.addWidget(search_group)
        params_layout.addStretch(1)
        self.tab_widget.addTab(tab_params, "⚙ 检索参数")

        # ================================================================
        layout.addWidget(self.tab_widget)

        # --- 提示 ---
        hint = QLabel(
            "💡 API Key 和模型选择写入 config/.env，参数设置写入 config/settings.yaml。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

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
        """根据当前提供商填充所有模型下拉框"""
        provider = self._current_provider
        models = get_provider_models(provider)
        items = []
        for model_name, display_name in models.items():
            items.append((f"{model_name} - {display_name}", model_name))

        def _fill(combo, current_model: str):
            combo.clear()
            combo.addItem("（使用默认模型）", "")
            for text, data in items:
                combo.addItem(text, data)
            idx = combo.findData(current_model)
            combo.setCurrentIndex(idx if idx >= 0 else 0)

        _fill(self.model_combo, self.settings.ai_model)
        _fill(self.query_model_combo, self.settings.ai_query_model)
        _fill(self.screen_model_combo, self.settings.ai_screen_model)
        _fill(self.analysis_model_combo, self.settings.ai_analysis_model)

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
                import re
                content = re.sub(
                    r'provider:\s*"[^"]*"',
                    f'provider: "{provider}"',
                    content
                )
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
                content = re.sub(
                    r'temperature:\s*\d+\.?\d*',
                    f'temperature: {temperature}',
                    content
                )
                # 更新各阶段专用模型
                for key, combo in [
                    ("query_model", self.query_model_combo),
                    ("screen_model", self.screen_model_combo),
                    ("analysis_model", self.analysis_model_combo),
                ]:
                    val = combo.currentData() or ""
                    if val:
                        if re.search(fr'{key}:\s*"[^"]*"', content):
                            content = re.sub(
                                fr'{key}:\s*"[^"]*"',
                                f'{key}: "{val}"',
                                content)
                        else:
                            # 不存在则添加到 ai 段
                            content = re.sub(
                                r'(temperature:\s*\d+\.?\d*)',
                                f'\\1\n  {key}: "{val}"',
                                content)
                yaml_path.write_text(content, encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "写入配置失败",
                f"无法更新设置文件:\n{e}")

        params = {
            "include_citations": self.include_citations_cb.isChecked(),
            "force_refresh": self.force_refresh_cb.isChecked(),
            "debug_search_only": self.debug_search_only_cb.isChecked(),
        }
        self.settings_saved.emit(provider, model, temperature, params)
        self.accept()
        stages = []
        for label, combo in [
            ("检索式生成", self.query_model_combo),
            ("筛选评分", self.screen_model_combo),
            ("对比分析", self.analysis_model_combo),
        ]:
            v = combo.currentData()
            stages.append(f"  {label}: {v if v else '（默认）'}")
        QMessageBox.information(self, "设置已保存",
            f"AI 引擎: {self.provider_combo.currentText()}\n"
            f"默认模型: {model}\n"
            f"温度: {temperature:.2f}\n\n"
            f"各阶段模型:\n" + "\n".join(stages) + "\n\n"
            f"检索参数:\n"
            f"  提取引用专利: {'是' if params['include_citations'] else '否'}\n"
            f"  强制重新获取: {'是' if params['force_refresh'] else '否'}\n"
            f"  仅搜索摘要: {'是' if params['debug_search_only'] else '否'}\n\n"
            "设置已写入 config/.env 和 config/settings.yaml，持续生效。")


class PromptEditorDialog(QDialog):
    """提示词模板编辑器 — 编辑 System/User Prompt 模板"""

    prompt_saved = Signal(str)  # 发射当前活跃的 profile 名称

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._current_profile = settings.prompts_active_profile
        self._profiles = self._scan_profiles()
        self._modified = False

        self.setWindowTitle("提示词配置")
        self.setMinimumSize(800, 600)
        self._setup_ui()
        self._load_current_profile()

    def _scan_profiles(self) -> list[str]:
        """扫描 prompts 目录下所有子文件夹作为 profile 列表"""
        prompts_dir = self.settings.prompts_dir
        if not prompts_dir.exists():
            return ["default"]
        profiles = sorted([
            p.name for p in prompts_dir.iterdir()
            if p.is_dir() and (p / "system.txt").exists()
        ])
        return profiles if profiles else ["default"]

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 顶部：方案选择 ──
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("提示词方案:"))
        self.profile_combo = QComboBox()
        for name in self._profiles:
            label = f"{name}（半导体专利）" if name == "semiconductor" else f"{name}（通用专利）"
            self.profile_combo.addItem(label, name)
        idx = self.profile_combo.findData(self._current_profile)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        top_bar.addWidget(self.profile_combo, 1)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ── 提示信息 ──
        hint = QLabel("💡 修改提示词会影响检索式生成质量。保存后下次检索生效。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 编辑区（Tab 切换 System / User） ──
        self.tab_widget = QTabWidget()
        font = self.font()
        font.setFamily("Consolas, Microsoft YaHei")
        font.setPointSize(10)

        self.system_edit = QPlainTextEdit()
        self.system_edit.setFont(font)
        self.system_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tab_widget.addTab(self.system_edit, "System Prompt")

        self.user_edit = QPlainTextEdit()
        self.user_edit.setFont(font)
        self.user_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tab_widget.addTab(self.user_edit, "User Prompt")

        layout.addWidget(self.tab_widget, 1)

        # ── 变量说明 ──
        var_label = QLabel(
            "可用变量: {patent_markdown}（专利全文Markdown）  "
            "{max_queries}（检索式数量）"
        )
        var_label.setWordWrap(True)
        var_label.setStyleSheet("color: #666; font-size: 11px; padding: 4px;")
        layout.addWidget(var_label)

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("恢复默认")
        reset_btn.setToolTip("恢复当前方案的提示词为出厂默认")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("saveBtn")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _load_current_profile(self):
        """加载当前选中方案的提示词到编辑器"""
        profile = self.profile_combo.currentData()
        if not profile:
            return
        self._current_profile = profile

        system_text = self.settings.get_prompt_text(profile, "system")
        user_text = self.settings.get_prompt_text(profile, "user")

        # 如果文件不存在，从 fallback 常量获取
        if not system_text:
            from src.query_generator.prompts import FALLBACK_SYSTEM_PROMPT
            system_text = FALLBACK_SYSTEM_PROMPT
        if not user_text:
            from src.query_generator.prompts import FALLBACK_USER_PROMPT
            user_text = FALLBACK_USER_PROMPT

        self.system_edit.setPlainText(system_text)
        self.user_edit.setPlainText(user_text)

    def _on_profile_changed(self):
        """切换方案 → 加载对应内容"""
        self._load_current_profile()

    def _on_reset(self):
        """恢复当前方案为出厂默认"""
        profile = self.profile_combo.currentData()
        if not profile:
            return

        reply = QMessageBox.question(
            self, "确认恢复",
            f"将「{profile}」方案的提示词恢复为出厂默认设置？\n当前修改将丢失。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from src.query_generator.prompts import FALLBACK_SYSTEM_PROMPT, FALLBACK_USER_PROMPT
        # 如果是 semiconductor 方案，没有代码级 fallback（只有文件级），
        # 恢复 = 删除文件让它重新从默认状态创建
        # 这里直接清空编辑器让用户重新保存
        if profile == "semiconductor":
            self.system_edit.setPlainText(FALLBACK_SYSTEM_PROMPT)
            self.user_edit.setPlainText(FALLBACK_USER_PROMPT)
        else:
            self.system_edit.setPlainText(FALLBACK_SYSTEM_PROMPT)
            self.user_edit.setPlainText(FALLBACK_USER_PROMPT)

    def _on_save(self):
        """保存当前编辑内容到磁盘"""
        profile = self.profile_combo.currentData()
        if not profile:
            return

        # 确保目录存在
        profile_dir = self.settings.prompts_dir / profile
        profile_dir.mkdir(parents=True, exist_ok=True)

        # 写入 system.txt
        system_path = profile_dir / "system.txt"
        system_path.write_text(self.system_edit.toPlainText(), encoding="utf-8")

        # 写入 user.txt
        user_path = profile_dir / "user.txt"
        user_path.write_text(self.user_edit.toPlainText(), encoding="utf-8")

        # 更新活跃方案
        yaml_path = self.settings.config_dir / "settings.yaml"
        if yaml_path.exists():
            content = yaml_path.read_text(encoding="utf-8")
            import re
            if re.search(r'prompt_profile:\s*"[^"]*"', content):
                content = re.sub(
                    r'prompt_profile:\s*"[^"]*"',
                    f'prompt_profile: "{profile}"',
                    content)
            else:
                content = re.sub(
                    r'(max_tokens:\s*\d+)',
                    f'\\1\n  prompt_profile: "{profile}"',
                    content)
            yaml_path.write_text(content, encoding="utf-8")

        self.prompt_saved.emit(profile)
        QMessageBox.information(self, "已保存",
            f"提示词方案「{profile}」已保存到:\n{profile_dir}\n\n下次检索生效。")
        self.accept()


class TestDialog(QDialog):
    """测试工具对话框 — 检索式测试、公布号查询等"""

    test_abstract = Signal(str, int)   # query, max_results
    test_detail = Signal(str, int)     # query, max_results
    test_pagesize = Signal(str, int)   # query, max_results (切换200条测试)
    lookup_patent = Signal(str)        # doc_id

    _HISTORY_FILE = Path(__file__).parent.parent.parent / "data" / "lookup_history.json"
    _MAX_HISTORY = 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("测试工具")
        self.setMinimumWidth(480)
        self._setup_ui()
        self._load_lookup_history()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 检索式测试 ──
        search_group = QGroupBox("检索式测试")
        search_layout = QVBoxLayout(search_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("检索式:"))
        self.test_query_edit = QLineEdit()
        self.test_query_edit.setPlaceholderText("输入 PATENTSCOPE 检索式...")
        self.test_query_edit.setText("掉电")
        row1.addWidget(self.test_query_edit, 1)
        search_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("数量上限:"))
        self.test_count_spin = QSpinBox()
        self.test_count_spin.setRange(1, 500)
        self.test_count_spin.setValue(10)
        row2.addWidget(self.test_count_spin)
        row2.addStretch(1)

        self.test_abstract_btn = QPushButton("🔍 测试摘要")
        self.test_abstract_btn.setToolTip("仅搜索摘要（快，验证检索式）")
        self.test_abstract_btn.clicked.connect(self._on_test_abstract)
        row2.addWidget(self.test_abstract_btn)

        self.test_detail_btn = QPushButton("📄 测试详情")
        self.test_detail_btn.setToolTip("搜索摘要 + 抓取全文详情")
        self.test_detail_btn.clicked.connect(self._on_test_detail)
        row2.addWidget(self.test_detail_btn)

        self.test_pagesize_btn = QPushButton("📐 测试200条")
        self.test_pagesize_btn.setToolTip("测试切换每页200条是否生效")
        self.test_pagesize_btn.clicked.connect(self._on_test_pagesize)
        row2.addWidget(self.test_pagesize_btn)
        search_layout.addLayout(row2)

        layout.addWidget(search_group)

        # ── 公布号直查 ──
        lookup_group = QGroupBox("公布号直查")
        lookup_layout = QHBoxLayout(lookup_group)

        self.lookup_combo = QComboBox()
        self.lookup_combo.setEditable(True)
        self.lookup_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lookup_combo.setMinimumWidth(200)
        self.lookup_combo.lineEdit().setPlaceholderText("输入公布号，如 WO2019006821...")
        lookup_layout.addWidget(self.lookup_combo, 1)

        self.lookup_btn = QPushButton("🔎 查看专利")
        self.lookup_btn.setToolTip("直接抓取专利详情并显示")
        self.lookup_btn.clicked.connect(self._on_lookup)
        lookup_layout.addWidget(self.lookup_btn)

        layout.addWidget(lookup_group)

        # ── 关闭 ──
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    # ── 公布号查询历史 ──────────────────────────────────────────────────

    def _load_lookup_history(self):
        """从文件加载公布号查询历史到下拉框"""
        try:
            if self._HISTORY_FILE.exists():
                data = json.loads(self._HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        self.lookup_combo.addItem(str(item))
        except Exception:
            pass

    def _save_lookup_history(self):
        """保存公布号查询历史到文件（最多 _MAX_HISTORY 条）"""
        items = []
        for i in range(self.lookup_combo.count()):
            items.append(self.lookup_combo.itemText(i))
        # 去重保序
        seen = set()
        unique = []
        for item in items:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        unique = unique[:self._MAX_HISTORY]
        try:
            self._HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._HISTORY_FILE.write_text(
                json.dumps(unique, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    # ── 按钮事件 ────────────────────────────────────────────────────────

    def _on_test_abstract(self):
        q = self.test_query_edit.text().strip()
        if q:
            self.test_abstract.emit(q, self.test_count_spin.value())

    def _on_test_detail(self):
        q = self.test_query_edit.text().strip()
        if q:
            self.test_detail.emit(q, self.test_count_spin.value())

    def _on_test_pagesize(self):
        q = self.test_query_edit.text().strip()
        if q:
            self.test_pagesize.emit(q, self.test_count_spin.value())

    def _on_lookup(self):
        doc_id = self.lookup_combo.currentText().strip()
        if doc_id:
            # 移到最前（去重）
            idx = self.lookup_combo.findText(doc_id)
            if idx >= 0:
                self.lookup_combo.removeItem(idx)
            self.lookup_combo.insertItem(0, doc_id)
            self.lookup_combo.setCurrentIndex(0)
            self._save_lookup_history()
            self.lookup_patent.emit(doc_id)
