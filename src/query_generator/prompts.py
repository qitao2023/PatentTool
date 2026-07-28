"""
AI 检索式生成 Prompt 模板 — PATENTSCOPE 简单搜索，中英关键词
"""
from src.pdf_extractor.extractor import PatentDocument


def build_system_prompt() -> str:
    return """你是一名中国专利审查员，需要在 PATENTSCOPE 中检索现有技术。

## 检索语法（PATENTSCOPE 简单搜索）

- 直接输入关键词，用 AND/OR/NOT 连接
- 精确短语用英文双引号："lithium battery"
- 通配符：* (多字符) ? (单字符)
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

## 输出格式

纯 JSON 对象，包含 queries 数组：

```json
{
  "queries": [
    {
      "query_string": "PATENTSCOPE检索式（纯关键词）",
      "search_angle": "角度说明",
      "rationale": "为什么这个检索式有效",
      "priority": 1
    }
  ]
}
```

- priority: 1 = 最宽泛/最重要，往后逐渐精准
- 第1个检索式必须最简单、最宽泛、保证有结果
"""


def build_user_prompt(patent: PatentDocument, max_queries: int = 10) -> str:
    patent_sections = []
    if patent.title:
        patent_sections.append(f"## 发明名称\n{patent.title}")
    if patent.abstract:
        patent_sections.append(f"## 摘要\n{patent.abstract}")
    if patent.claims:
        patent_sections.append(f"## 权利要求书\n" + "\n".join(patent.claims))
    if patent.ipc_classifications:
        patent_sections.append(f"## IPC分类号\n" + ", ".join(patent.ipc_classifications))
    if patent.applicants:
        patent_sections.append(f"## 申请人\n" + ", ".join(patent.applicants))
    if patent.inventors:
        patent_sections.append(f"## 发明人\n" + ", ".join(patent.inventors))
    if patent.description:
        patent_sections.append(f"## 说明书\n{patent.description}")

    patent_markdown = "\n\n".join(patent_sections)

    return f"""# 专利技术方案分析

{patent_markdown}

---

# 任务

你是一名专利审查员，需要在 PATENTSCOPE 中检索和本申请相关的现有技术。

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
