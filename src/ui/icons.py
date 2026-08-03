"""
内嵌 SVG 图标模块 —— 统一控件/菜单图标风格。

- 全部图标为手写 SVG 字符串，线条风（Lucide/Feather 风格），视觉统一。
- 用 QSvgRenderer 直接渲染 SVG 字节 → QPixmap → QIcon，**不依赖外部图标文件**，
  打包（PyInstaller）后依然可用；无需改 spec 的 datas。
- 图标颜色通过占位符 __COLOR__ 替换，可为深蓝/白/灰，适配不同按钮底色。

使用方式：
    from src.ui.icons import icon, app_icon
    btn.setIcon(icon("play"))            # 默认 18px 深蓝
    btn.setIcon(icon("folder", "#fff"))  # 白图标（用于蓝色按钮）
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer

# 主题默认图标色
_DEFAULT_COLOR = "#57606A"

_SVG_STROKE_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="__COLOR__" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
)

# 图标名 → 内部内容（body）。全部基于 24x24 画布。
_ICON_BODIES = {
    "folder": (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8'
        'a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    ),
    "play": (
        '<polygon points="8 5 20 12 8 19 8 5" stroke-linejoin="round"/>'
    ),
    "stop": (
        '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>'
    ),
    "trash": (
        '<path d="M4 7h16"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6'
        'a2 2 0 0 0 2-2l1-12"/><path d="M9 7V5a2 2 0 0 1 2-2h2'
        'a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/>'
        '<line x1="14" y1="11" x2="14" y2="17"/>'
    ),
    "settings": (
        '<line x1="4" y1="7" x2="20" y2="7"/>'
        '<line x1="4" y1="12" x2="20" y2="12"/>'
        '<line x1="4" y1="17" x2="20" y2="17"/>'
        '<circle cx="8" cy="7" r="2.1" fill="__COLOR__" stroke="none"/>'
        '<circle cx="16" cy="12" r="2.1" fill="__COLOR__" stroke="none"/>'
        '<circle cx="10" cy="17" r="2.1" fill="__COLOR__" stroke="none"/>'
    ),
    "calendar": (
        '<rect x="4" y="5" width="16" height="16" rx="2"/>'
        '<line x1="4" y1="10" x2="20" y2="10"/>'
        '<line x1="9" y1="3" x2="9" y2="6"/><line x1="15" y1="3" x2="15" y2="6"/>'
    ),
    "clock": (
        '<circle cx="12" cy="12" r="8"/><path d="M12 7v5l3.5 2"/>'
    ),
    "book": (
        '<path d="M3 5a2 2 0 0 1 2-2h5v16H5a2 2 0 0 1-2-2z"/>'
        '<path d="M21 5a2 2 0 0 0-2-2h-5v16h5a2 2 0 0 0 2-2z"/>'
    ),
    "star": (
        '<polygon points="12 3 15 9 21 9.5 16.5 14 18 20 12 16.5 '
        '6 20 7.5 14 3 9.5 9 9"/>'
    ),
    "file_text": (
        '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'
        '<line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>'
    ),
    "flask": (
        '<path d="M9 3h6"/><path d="M10 3v6L5 18a2 2 0 0 0 1.8 3h10.4'
        'A2 2 0 0 0 19 18L14 9V3"/><path d="M7.5 15h9"/>'
    ),
    "log_out": (
        '<path d="M15 12H3"/><path d="M10 7l-5 5 5 5"/>'
        '<path d="M15 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/>'
    ),
    "info": (
        '<circle cx="12" cy="12" r="9"/>'
        '<circle cx="12" cy="7.5" r="0.9" fill="__COLOR__" stroke="none"/>'
        '<line x1="12" y1="11" x2="12" y2="17"/>'
    ),
    "robot": (
        '<rect x="5" y="8" width="14" height="11" rx="3"/>'
        '<circle cx="9.2" cy="13.5" r="1.1" fill="__COLOR__" stroke="none"/>'
        '<circle cx="14.8" cy="13.5" r="1.1" fill="__COLOR__" stroke="none"/>'
        '<line x1="12" y1="8" x2="12" y2="5"/><line x1="8" y1="5" x2="16" y2="5"/>'
        '<circle cx="6.5" cy="6" r="1" fill="__COLOR__" stroke="none"/>'
        '<circle cx="17.5" cy="6" r="1" fill="__COLOR__" stroke="none"/>'
    ),
    "download": (
        '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><line x1="5" y1="20" x2="19" y2="20"/>'
    ),
    "refresh": (
        '<path d="M4.5 12a7.5 7.5 0 0 1 12.9-5.2L20 9.3"/>'
        '<path d="M20 3.5V9.3h-5.8"/>'
        '<path d="M19.5 12a7.5 7.5 0 0 1-12.9 5.2L4 14.7"/>'
        '<path d="M4 20.5V14.7h5.8"/>'
    ),
    "search": (
        '<circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/>'
    ),
    "doc": (
        '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>'
        '<line x1="9" y1="11" x2="15" y2="11"/><line x1="9" y1="15" x2="15" y2="15"/>'
    ),
    "chevron_right": (
        '<polyline points="9 6 15 12 9 18"/>'
    ),
}

# 应用图标（彩色填充版，用于窗口图标与 exe 图标）
_APP_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">
  <rect x="1.5" y="1.5" width="45" height="45" rx="11" fill="#0B6BCB"/>
  <rect x="11" y="7" width="26" height="34" rx="4" fill="#FFFFFF"/>
  <rect x="11" y="7" width="6" height="34" rx="3" fill="#DCEBFB"/>
  <rect x="21" y="14" width="12" height="2.8" rx="1.4" fill="#0B6BCB"/>
  <rect x="21" y="20" width="12" height="2.8" rx="1.4" fill="#0B6BCB"/>
  <rect x="21" y="26" width="12" height="2.8" rx="1.4" fill="#0B6BCB"/>
  <rect x="21" y="32" width="7.5" height="2.8" rx="1.4" fill="#0B6BCB"/>
</svg>
"""


def _build_svg(body: str, color: str) -> str:
    svg = _SVG_STROKE_HEAD.replace("__COLOR__", color) + body + "</svg>"
    return svg


@lru_cache(maxsize=256)
def _pixmap(body: str, color: str, size: int) -> QPixmap:
    """把 SVG 渲染成指定尺寸的透明 QPixmap。"""
    svg_data = _build_svg(body, color) if body != "app" else _APP_SVG
    renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return pix


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    """获取命名图标。color 为空用默认深灰色；可传 '#FFFFFF' 用于彩色按钮。"""
    body = _ICON_BODIES.get(name)
    if body is None:
        return QIcon()
    qicon = QIcon()
    for s in (size - 2, size, size + 2):
        if s >= 8:
            qicon.addPixmap(_pixmap(body, color or _DEFAULT_COLOR, s))
    return qicon


def app_icon() -> QIcon:
    """应用图标（多尺寸，供窗口/任务栏使用）。"""
    qicon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        qicon.addPixmap(_pixmap("app", "", s))
    return qicon


def app_svg() -> str:
    """应用图标 SVG 源码（生成 exe 图标用）。"""
    return _APP_SVG
