"""
专利文本格式化器：将紧凑文本渲染为可读 HTML，供界面 QTextBrowser 显示。
"""

import re

# 专利说明书的章节标题
_SECTION_HEADERS = [
    '技术领域', '背景技术', '发明内容', '附图说明', '具体实施方式',
    '有益效果', '附图标记', '权利要求书',
]

# 英文变体（备用）
_SECTION_HEADERS_EN = [
    'Technical Field', 'Background Art', 'Summary of Invention',
    'Brief Description of Drawings', 'Detailed Description',
    'Claims', 'Description of Embodiments',
]


def format_patent_text(text: str, section_type: str = "description") -> str:
    """将紧凑的专利文本格式化为可读 HTML。

    Args:
        text: clean_patent_html_text() 输出的紧凑文本
        section_type: "claims" 或 "description"

    Returns:
        带 CSS 样式的 HTML 字符串
    """
    if not text:
        return '<div style="color:#888;padding:20px;">（无内容）</div>'

    # 先做 HTML 转义
    text = _escape_html(text)

    # ── 插入章节标题 ──────────────────────────────────────────────
    for h in _SECTION_HEADERS + _SECTION_HEADERS_EN:
        # 章节标题前后加段落分隔 + 加粗
        # 前置可以是：开头、句号、分号、空格、或 > 标签
        text = re.sub(
            rf'(^|[。；]|(?<=\s)|>)\s*{re.escape(h)}\s*',
            rf'\1</p><h3>{h}</h3><p>',
            text
        )

    # ── Claims 特殊处理：权利要求编号 ──────────────────────────────
    if section_type == "claims":
        text = re.sub(r'^权利要求书\s*', '', text)
        text = re.sub(r'^<p>权利要求书\s*', '<p>', text)

        # WO 格式: [权利要求 N] → 分段 + 加粗
        text = re.sub(r'(\[权利要求\s*\d+\])', r'</p><p><b>\1</b> ', text)
        text = re.sub(r'(\[Claim\s*\d+\])', r'</p><p><b>\1</b> ', text)
        # CN 编号: 。 1.xxx ；2.xxx → 分段落
        text = re.sub(r'([。；])\s*(\d+\.[\s ]*)', r'\1</p><p><b>\2</b>', text)
        # 开头第一条: <p>1.xxx → 编号加粗
        text = re.sub(r'(<p>)\s*(\d+\.[\s ]*)', r'\1<b>\2</b>', text)

    # ── Description: 段落分隔 ──────────────────────────────────────
    else:
        # 按 [NNNN] 段落编号分段
        text = re.sub(r'(\[\d{4}\])', r'</p><p>\1', text)
        # 识别从属权利要求引用，前面加分段
        text = re.sub(r'(根据权利要求\d+[^。；]*[。；])', r'</p><p>\1', text)

    # ── 确保有起始 <p> 标签 ────────────────────────────────────────
    if not text.startswith('<'):
        text = '<p>' + text
    if not text.rstrip().endswith('</p>'):
        text = text.rstrip() + '</p>'

    # ── 清理空标签和多余标记 ──────────────────────────────────────────
    text = re.sub(r'^</p>', '', text)        # 去除开头多余的 </p>
    text = re.sub(r'<p>\s*</p>', '', text)
    text = re.sub(r'<h3>\s*</h3>', '', text)

    # ── 包装 HTML ───────────────────────────────────────────────────
    css = """
        body {
            font-family: "Microsoft YaHei", "SimSun", sans-serif;
            line-height: 1.9;
            padding: 8px 12px;
            color: #222;
        }
        h3 {
            color: #1a5276;
            font-size: 15px;
            margin: 16px 0 8px 0;
            padding-bottom: 4px;
            border-bottom: 1px solid #d4e6f1;
        }
        p {
            margin: 6px 0;
            text-indent: 2em;
        }
        p:first-child { margin-top: 0; }
        b { color: #b03a2e; }
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>{text}</body></html>"""


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    return text
