"""
集中式浅色主题模块 —— 全部界面颜色/字体/圆角在此定义。

设计语言：现代扁平化 Win11 风格（浅色）。
- 用占位符 token（如 @accent）拼装 QSS，避免 f-string 大括号转义。
- 颜色集中在一个字典里，未来要加深色模式只需新增一套 token 并替换。
- 使用方式：
    from src.ui.theme import build_qss, apply_theme
    apply_theme(app)          # 相当于 app.setStyleSheet(build_qss())
"""
from __future__ import annotations

# ── 颜色 token ───────────────────────────────────────────────────────────
# 语义化命名，方便整组替换
DEFAULT_THEME = {
    # 品牌主色
    "accent": "#0B6BCB",            # 主蓝色（按钮/选中/焦点）
    "accent_hover": "#095FB4",
    "accent_pressed": "#08529E",
    "accent_grad_hi": "#1B86E0",    # 主按钮渐变上
    "accent_grad_lo": "#0B6BCB",    # 主按钮渐变下
    "accent_soft": "#E7F1FB",       # 选中态浅蓝底
    "accent_soft_border": "#B9D9F5",
    # 背景 / 卡片
    "bg": "#F5F6F8",                # 窗口背景
    "card": "#FFFFFF",              # 卡片/面板
    "surface": "#F0F3F7",           # 次级面（表头/状态栏）
    "surface_hover": "#E3E8EE",
    # 边框
    "border": "#D7DDE3",            # 弱边框（卡片）
    "border_strong": "#B9C0C8",     # 输入控件边框
    # 文字
    "text": "#24292F",
    "text_secondary": "#57606A",
    "text_muted": "#8B949E",
    "on_accent": "#FFFFFF",
    # 语义色
    "danger": "#D93939",
    "danger_hover": "#C22B2B",
    "danger_pressed": "#A52424",
    "success": "#1A7F37",
    "warning": "#B45309",
    # 日志分级色
    "log_info": "#0B6BCB",
    "log_warn": "#B45309",
    "log_error": "#C53030",
    "log_success": "#1A7F37",
    "log_debug": "#8B949E",
    # 其他
    "disabled_bg": "#F0F0F0",
    "disabled_text": "#B6BDC5",
    "scrollbar": "#C4CBD4",
    "scrollbar_hover": "#9AA4B0",
    "zebra": "#F8FAFC",
    "splitter": "#C9D1D9",
}

# 日志级别 → 颜色 token 映射（log_panel 着色用）
LEVEL_TOKENS = {
    "INFO": "log_info",
    "WARN": "log_warn",
    "ERROR": "log_error",
    "SUCCESS": "log_success",
    "DEBUG": "log_debug",
}


def _apply_tokens(qss: str, tokens: dict) -> str:
    """替换 @xxx 占位符。按 key 长度降序替换，避免 @accent 抢先吞掉 @accent_soft。"""
    for key in sorted(tokens, key=len, reverse=True):
        qss = qss.replace(f"@{key}", tokens[key])
    return qss


def build_qss(tokens: dict | None = None) -> str:
    """构建全局 QSS 字符串。传入自定义 token 字典可整体换肤。"""
    t = tokens or DEFAULT_THEME
    qss = _apply_tokens(_QSS_TEMPLATE, t)
    return qss


def apply_theme(app, tokens: dict | None = None) -> None:
    """给 QApplication 套上主题样式。"""
    app.setStyleSheet(build_qss(tokens))


_QSS_TEMPLATE = """
/* ═══════════════ 全局 ═══════════════ */
* {
    font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans SC", sans-serif;
    font-size: 13px;
    color: @text;
}
QMainWindow {
    background-color: @bg;
}
/* 对话框统一背景（普通 QWidget 面板不设背景、天然透出主窗口底色） */
QDialog {
    background-color: @bg;
}

/* ═══════════════ 菜单栏 ═══════════════ */
QMenuBar {
    background: @bg;
    border-bottom: 1px solid @border;
    padding: 2px 4px;
    font-size: 13px;
}
QMenuBar::item {
    padding: 5px 12px;
    border-radius: 5px;
    background: transparent;
}
QMenuBar::item:selected {
    background: @accent_soft;
    color: @accent;
}
QMenuBar::item:pressed {
    background: @surface_hover;
}
QMenu {
    background: @card;
    border: 1px solid @border;
    border-radius: 8px;
    padding: 6px;
    font-size: 13px;
}
QMenu::item {
    padding: 6px 28px 6px 12px;
    border-radius: 5px;
    background: transparent;
}
QMenu::item:selected {
    background: @accent_soft;
    color: @accent;
}
QMenu::separator {
    height: 1px;
    background: @border;
    margin: 5px 8px;
}

/* ═══════════════ 分组框（卡片） ═══════════════ */
QGroupBox {
    font-weight: bold;
    font-size: 13px;
    border: 1px solid @border;
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    background: @card;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: @text;
    background: transparent;
}

/* ═══════════════ 按钮 ═══════════════ */
QPushButton {
    padding: 6px 16px;
    min-height: 16px;   /* 内容最小高，使总高统一为 30px */
    border: 1px solid @border_strong;
    border-radius: 6px;
    background: @card;
    color: @text;
    font-size: 13px;
}
QPushButton:hover {
    background: @surface_hover;
    border-color: #9AA6B2;
}
QPushButton:pressed {
    background: #D6DEE6;
    border-color: #8B97A3;
}
QPushButton:disabled {
    background: @disabled_bg;
    color: @disabled_text;
    border-color: @border;
}
QPushButton:focus {
    outline: none;
    border: 1.5px solid @accent;
}

/* 主操作按钮（开始） */
QPushButton#startBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @accent_grad_hi, stop:1 @accent_grad_lo);
    color: @on_accent;
    font-weight: bold;
    font-size: 13px;
    padding: 8px 28px;
    border: none;
    border-radius: 6px;
    min-height: 24px;   /* 保持主按钮 40px 高 */
}
QPushButton#startBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2E96E8, stop:1 @accent_hover);
}
QPushButton#startBtn:pressed {
    background: @accent_pressed;
}
QPushButton#startBtn:disabled {
    background: #C8D0D8;
    color: #E8ECF0;
    border: none;
}

/* 停止按钮 */
QPushButton#stopBtn {
    background: @danger;
    color: @on_accent;
    font-weight: bold;
    font-size: 13px;
    padding: 8px 20px;
    border: none;
    border-radius: 6px;
    min-height: 24px;   /* 保持停止按钮 40px 高 */
}
QPushButton#stopBtn:hover {
    background: @danger_hover;
}
QPushButton#stopBtn:pressed {
    background: @danger_pressed;
}
QPushButton#stopBtn:disabled {
    background: #C8D0D8;
    color: #E8ECF0;
}

/* 次级按钮（设置 / 导出） */
QPushButton#exportBtn, QPushButton#settingsBtn {
    background: @surface;
    color: @text_secondary;
    border: 1px solid @border_strong;
    padding: 6px 16px;
}
QPushButton#settingsBtn { min-height: 22px; }   /* 保持设置按钮 36px 高 */
QPushButton#exportBtn:hover, QPushButton#settingsBtn:hover {
    background: @surface_hover;
    border-color: #9AA6B2;
}

/* 保存按钮 */
QPushButton#saveBtn {
    background: @accent;
    color: @on_accent;
    font-weight: bold;
    padding: 6px 24px;
    border: none;
    border-radius: 6px;
}
QPushButton#saveBtn:hover {
    background: @accent_hover;
}

/* 视图切换胶囊按钮（专利详情 / AI 分析） */
QPushButton#viewToggleBtn {
    padding: 5px 16px;
    min-height: 18px;   /* 总高 30px，与导出按钮一致 */
    border: 1px solid @border_strong;
    border-radius: 16px;
    background: @card;
    color: @text_secondary;
}
QPushButton#viewToggleBtn:hover {
    border-color: @accent;
    color: @accent;
}
QPushButton#viewToggleBtn:checked {
    background: @accent;
    color: @on_accent;
    border-color: @accent;
    font-weight: bold;
}

/* ═══════════════ 标签 ═══════════════ */
QLabel { color: @text; }
QLabel#hintLabel { color: @text_muted; }
QLabel#aiProviderLabel {
    color: @accent;
    font-weight: bold;
    background: @accent_soft;
    padding: 3px 10px;
    border-radius: 10px;
}

/* ═══════════════ 输入控件 ═══════════════ */
QLineEdit, QPlainTextEdit {
    padding: 6px 10px;
    border: 1px solid @border_strong;
    border-radius: 6px;
    background: @card;
    font-size: 13px;
    selection-background-color: @accent_soft;
    selection-color: @accent;
}
/* 单行输入框与按钮统一高度 30px（min-height 为内容高，总高=内容+padding+border） */
QLineEdit { min-height: 16px; }
QLineEdit:hover, QPlainTextEdit:hover { border-color: #9AA6B2; }
QLineEdit:focus, QPlainTextEdit:focus {
    border-color: @accent;
    border-width: 1.5px;
}

/* 数字框/下拉框统一高度 30px：spin 靠 padding4+minH17，combo 靠 padding4+minH20 */
QSpinBox, QDoubleSpinBox, QComboBox {
    padding: 4px 10px;
    border: 1px solid @border_strong;
    border-radius: 6px;
    background: @card;
    font-size: 13px;
}
QSpinBox, QDoubleSpinBox { min-height: 17px; }
QComboBox { min-height: 20px; }
QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover { border-color: #9AA6B2; }
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: @accent; }
QSpinBox::up-button, QSpinBox::down-button {
    width: 20px;
    background: transparent;
}
QComboBox::drop-down {
    width: 24px;
    border: none;
}
QComboBox QAbstractItemView {
    border: 1px solid @border;
    border-radius: 6px;
    padding: 4px;
    background: @card;
    selection-background-color: @accent_soft;
    selection-color: @accent;
    outline: none;
}

QCheckBox {
    spacing: 6px;
}

/* ═══════════════ 表格 ═══════════════ */
QTableWidget {
    border: 1px solid @border;
    border-radius: 8px;
    background: @card;
    gridline-color: @border;
    font-size: 13px;
    outline: none;
}
QTableWidget::item {
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid #EEF1F4;
}
QTableWidget::item:selected {
    background: @accent_soft;
    color: @text;
    border-left: 3px solid @accent;
}
QTableWidget::item:hover:!selected {
    background: @zebra;
}
QHeaderView::section {
    background: @surface;
    padding: 7px 8px;
    border: none;
    border-bottom: 2px solid @border_strong;
    font-weight: bold;
    font-size: 13px;
    color: @text_secondary;
}

/* ═══════════════ 标签页 ═══════════════ */
QTabWidget::pane {
    border: 1px solid @border;
    border-radius: 8px;
    background: @card;
    top: -1px;
}
QTabBar::tab {
    padding: 8px 18px;
    border: 1px solid transparent;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    background: transparent;
    margin-right: 2px;
    font-size: 13px;
    color: @text_secondary;
}
QTabBar::tab:hover:!selected {
    background: @surface;
    color: @text;
}
QTabBar::tab:selected {
    background: @card;
    color: @accent;
    font-weight: bold;
    border: 1px solid @border;
    border-bottom: 2px solid @accent;
}

/* ═══════════════ 文本浏览器 ═══════════════ */
QTextBrowser {
    border: 1px solid @border;
    border-radius: 8px;
    background: @card;
}

/* ═══════════════ 滑块 ═══════════════ */
QSlider::groove:horizontal {
    height: 6px;
    background: @border;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -5px 0;
    background: @accent;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: @accent_hover; }

/* ═══════════════ 分割器 ═══════════════ */
QSplitter::handle {
    background: @splitter;
}
QSplitter::handle:hover {
    background: @accent;
}

/* ═══════════════ 进度条 ═══════════════ */
QProgressBar {
    border: 1px solid @border_strong;
    border-radius: 6px;
    background: @surface;
    text-align: center;
    font-weight: bold;
    font-size: 12px;
    color: @text_secondary;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 @accent_grad_hi, stop:1 @accent_grad_lo);
    border-radius: 5px;
}

/* ═══════════════ 状态栏 ═══════════════ */
QStatusBar {
    background: @surface;
    border-top: 1px solid @border;
    font-size: 12px;
    color: @text_secondary;
}
QStatusBar::item { border: none; }

/* ═══════════════ 滚动条 ═══════════════ */
QScrollBar:vertical {
    width: 10px;
    background: transparent;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: @scrollbar;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: @scrollbar_hover; }
QScrollBar:horizontal {
    height: 10px;
    background: transparent;
}
QScrollBar::handle:horizontal {
    background: @scrollbar;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: @scrollbar_hover; }
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0px;
    width: 0px;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

/* ═══════════════ 工具提示 ═══════════════ */
QToolTip {
    background: @text;
    color: @card;
    border: none;
    border-radius: 4px;
    padding: 5px 8px;
    font-size: 12px;
}
"""
