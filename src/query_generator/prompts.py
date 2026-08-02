"""
AI 检索式生成 Prompt 模板 — 从 config/prompts/<profile>/*.txt 加载，支持多方案切换
"""
from src.pdf_extractor.extractor import PatentDocument

# ============================================================
# Fallback 默认提示词（当配置文件中不存在时使用）
# ============================================================

FALLBACK_SYSTEM_PROMPT = """你是一名中国专利审查员，需要在 Google Patents 中检索现有技术。

## 检索语法（Google Patents）

- 直接输入关键词，用 AND/OR/NOT 连接（默认 AND，建议用括号分组：`(a OR b) AND c`）
- 精确短语用英文双引号："lithium battery"
- 通配符：`*`（0 或多个字符）`?`（0 或 1 个字符），仅限单个英文单词
- 排除词用 `-` 前缀：`-shovel`
- 中英文混合：可以在同一个检索式中同时使用中文和英文关键词

## 核心原则

### 原则1：先保证有结果
第一个检索式只提取最核心的 2-3 个关键词，宁宽勿窄。

### 原则2：关键词扩展
对核心技术词做同义词/上下位扩展，用 OR 连接：
`(pressure OR stress) AND (detection OR measurement OR sensing)`

### 原则3：中英文都要覆盖
如果专利是中文，第一个检索式必须包含中文关键词：
`(压力测试 OR stress test) AND 掉电 AND (闪存 OR flash OR 存储器)`

### 原则4：一个角度一个检索式，越简单越好
- 每个检索式关键词数 ≤ 5 个
- AND/OR 运算符 ≤ 2-3 个
- 不要嵌套太深
"""

FALLBACK_USER_PROMPT = """# 专利技术方案分析

{patent_markdown}

---

# 任务

需要在 Google Patents 中检索和本申请相关的现有技术。

## 检索式构建原则

### 原则1：先宽后窄
第1个检索式必须是最宽泛的，只用核心关键词。

### 原则2：充分扩展
同义词、上下位概念用 OR 连接。

### 原则3：中英文覆盖
如果专利是中文，检索式要同时包含中文和英文关键词。

### 原则4：越简单越好
每个检索式最多 3-5 个关键词，2-3 个运算符。

## 检索式角度示例

- **角度1（必选）**：核心关键词宽泛检索 → `(压力测试 OR stress test) AND 掉电 AND (闪存 OR flash)`
- **角度2（必选）**：不同表达方式 → `(断电 OR power loss OR power failure) AND (存储器测试 OR memory test)`
- **角度3（可选）**：特定技术特征 → `随机断电 AND 压力测试 AND 闪存`

## 输出格式

纯JSON对象，包含queries数组（不要markdown代码块包裹）：

```json
{{
  "queries": [
    {{
      "query_string": "检索式（纯关键词，AND/OR连接，支持双引号精确匹配）",
      "search_angle": "角度说明",
      "rationale": "为什么这个检索式有效",
      "priority": 1
    }}
  ]
}}
```

- 必须恰好生成 {max_queries} 个检索式，一个都不能少
- 如果本申请技术特征不够支持 {max_queries} 个角度，就从 IPC 分类、申请人、相近技术领域等角度扩展
- priority: 1 = 最宽泛/最重要
- 第1个检索式必须是最简单、最宽泛、保证有结果的
"""


# ============================================================
# 公共函数
# ============================================================

def _truncate_description(description: str, max_chars: int) -> str:
    """智能截断说明书：优先保留"发明内容"，次选"具体实施方式"前段

    中国专利说明书结构：
      技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式
    其中"发明内容"与权利要求对应，是检索式生成最有价值的部分。
    """
    if not description or len(description) <= max_chars:
        return description or ""

    import re

    # 策略1: 提取"发明内容"部分
    invention_match = re.search(
        r'(?:发明内容|发明概述).*?(?=\n(?:附图说明|具体实施方式|实施例|图\d|Brief Description|DETAILED DESCRIPTION|Detailed Description|附图中|以下结合))',
        description, re.IGNORECASE | re.DOTALL
    )

    if invention_match and len(invention_match.group()) > 200:
        invention_text = invention_match.group().strip()
        remaining = max_chars - len(invention_text)

        if remaining > 500:
            # 策略2: 还有空间，追加"具体实施方式"前段
            after_invention = description[invention_match.end():]
            impl_match = re.search(
                r'(?:具体实施方式|DETAILED DESCRIPTION|Detailed Description).*',
                after_invention, re.IGNORECASE | re.DOTALL
            )
            if impl_match:
                impl_text = impl_match.group()[:remaining].strip()
                return (invention_text + "\n\n## 具体实施方式（前段）\n" +
                        impl_text +
                        f"\n\n---\n[说明书已智能截断，保留约 {max_chars} 字符]")
        return invention_text + "\n\n---\n[说明书已截断]"

    # 策略3（降级）: 未找到标准段落结构 → 取前 max_chars
    return description[:max_chars] + "\n\n---\n[说明书已截断]"


def build_patent_markdown(patent: PatentDocument,
                          max_description_chars: int = 5000) -> str:
    """从 PatentDocument 提取信息拼接为 Markdown

    Args:
        patent: 专利文档
        max_description_chars: 说明书截断长度，0 表示不截断
    """
    sections = []
    if patent.title:
        sections.append(f"## 发明名称\n{patent.title}")
    if patent.abstract:
        sections.append(f"## 摘要\n{patent.abstract}")
    if patent.claims:
        # 权利要求1 单独标注，引导 AI 重点关注
        claims_text = patent.claims.copy()
        if claims_text:
            claims_text[0] = f"【权利要求1 — 独立权利要求，本申请最核心保护范围】\n{claims_text[0]}"
        sections.append("## 权利要求书\n" + "\n\n".join(claims_text))
    if patent.ipc_classifications:
        sections.append("## IPC分类号\n" + ", ".join(patent.ipc_classifications))
    if patent.applicants:
        sections.append("## 申请人\n" + ", ".join(patent.applicants))
    if patent.inventors:
        sections.append("## 发明人\n" + ", ".join(patent.inventors))
    if patent.description:
        desc = _truncate_description(patent.description, max_description_chars) \
            if max_description_chars > 0 else patent.description
        sections.append(f"## 说明书\n{desc}")
    return "\n\n".join(sections)


# ============================================================
# 方案解析：default(通用) / semiconductor(半导体) / auto(按专利内容自动判断)
# ============================================================

# 半导体 IPC 分类号前缀（含 2023 年 IPC 拆分出的 H10* 新分类）
_SEMICONDUCTOR_IPC_PREFIXES = (
    "H01L", "H10B", "H10K", "H10N", "G11C", "H01S", "B81B", "B81C",
)

# 标题/摘要中的半导体强特征词（中英）
_SEMICONDUCTOR_KEYWORDS = (
    # 中文
    "半导体", "晶体管", "芯片", "晶圆", "集成电路", "存储器", "闪存", "内存",
    "微机电", "光刻", "刻蚀", "蚀刻", "外延", "氮化镓", "碳化硅", "砷化镓",
    "多晶硅", "单晶硅", "光电", "发光二极管", "激光器", "太阳能电池",
    "场效应", "鳍式", "栅极", "源漏", "功率器件",
    # 英文
    "semiconductor", "transistor", "finfet", "mosfet", "igbt", "chip",
    "wafer", "integrated circuit", "memory", "flash", "microelectromechanical",
    "mems", "lithography", "etching", "epitaxial", "gallium", "silicon",
    "nitride", "dielectric", "cmos", "led", "laser", "photovoltaic",
    "solar cell", "oled", "thin film", "tft", "tsv",
)


def _is_semiconductor_patent(patent) -> bool:
    """按专利内容判断是否半导体领域（IPC 分类号 + 标题/摘要关键词）"""
    if patent is None:
        return False
    for ipc in (getattr(patent, "ipc_classifications", None) or []):
        code = str(ipc).replace(" ", "").upper()
        for prefix in _SEMICONDUCTOR_IPC_PREFIXES:
            if code.startswith(prefix):
                return True
    text = " ".join(filter(None, [
        getattr(patent, "title", None) or "",
        getattr(patent, "abstract", None) or "",
    ])).lower()
    for kw in _SEMICONDUCTOR_KEYWORDS:
        if kw.lower() in text:
            return True
    return False


def resolve_profile(settings, patent=None) -> str:
    """解析实际使用的检索式方案

    settings.prompts_active_profile:
      auto          → 按专利内容自动判断（半导体/通用）
      default       → 固定通用检索式
      semiconductor → 固定半导体检索式
    未知值回退到 semiconductor（当前默认）。
    """
    mode = settings.prompts_active_profile
    if mode == "auto":
        return "semiconductor" if _is_semiconductor_patent(patent) else "default"
    if mode in ("default", "semiconductor"):
        return mode
    return "semiconductor"


def load_prompt_templates(settings, patent=None) -> dict:
    """从 Settings 加载实际使用的 profile 提示词模板

    Args:
        settings: Settings 对象
        patent: PatentDocument，auto 方案按专利内容解析（可为 None，则按固定方案）
    Returns:
        {"system": str, "user": str}
    """
    profile = resolve_profile(settings, patent)
    system = settings.get_prompt_text(profile, "system") or FALLBACK_SYSTEM_PROMPT
    user = settings.get_prompt_text(profile, "user") or FALLBACK_USER_PROMPT
    return {"system": system, "user": user}


def build_system_prompt(settings=None, patent=None) -> str:
    """构建 System Prompt

    Args:
        settings: Settings 对象。为 None 时使用 fallback 默认值。
        patent: PatentDocument，auto 方案按专利内容解析。
    """
    if settings:
        return load_prompt_templates(settings, patent)["system"]
    return FALLBACK_SYSTEM_PROMPT


def build_user_prompt(patent: PatentDocument, max_queries: int = 10,
                      settings=None) -> str:
    """构建 User Prompt（含专利信息和变量替换）

    Args:
        patent: 专利文档
        max_queries: 最大检索式数量
        settings: Settings 对象。为 None 时使用 fallback 默认值。
    """
    # 0 = 不截断，说明书全文注入（默认）；>0 才截断
    max_desc = settings.query_max_description_chars if settings else 0
    patent_md = build_patent_markdown(patent, max_description_chars=max_desc)
    if settings:
        template = load_prompt_templates(settings, patent)["user"]
    else:
        template = FALLBACK_USER_PROMPT
    return template.format(patent_markdown=patent_md, max_queries=max_queries)
