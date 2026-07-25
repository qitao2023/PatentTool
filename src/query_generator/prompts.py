"""
AI 检索式生成 Prompt 模板 — 简单优先，保证有结果
"""
from src.pdf_extractor.extractor import PatentDocument


def build_system_prompt() -> str:
    return """你是一名中国专利审查员，需要检索和本申请相关的现有技术，
最好能够评述本申请的新颖性和创造性。

## 核心要求：简单优先
1. 每个检索式要确保 **能搜到结果**，宁愿结果多一些，也不要搜不到
2. 优先使用 **简单的关键词组合 + /A 字段**（/A = 标题+摘要+权利要求）
3. **先宽后窄**：先用宽泛检索式确保有结果，再用精准的缩小范围
4. 不要使用复杂的邻近算子（nW/nD/S/P），HimmPat 对这些支持不稳定
5. 不要堆砌太多 AND/NOT 条件，每个检索式最多用 1-2 个运算符
6. 关键词要充分扩展（同义词、上下位概念），用 OR 连接扩展词

## 推荐句式
- 推荐: `(关键词1 关键词2)/A AND (IPC分类号)/IC`
- 推荐: `(关键词1 OR 同义词1 OR 同义词2)/A`
- 推荐: `(关键词1 关键词2)/TI`
- 不推荐: `((关键词1 3W 关键词2)/CLMS AND (关键词3 5D 关键词4)/B) NOT ...`"""


def build_user_prompt(patent: PatentDocument, max_queries: int = 10) -> str:
    # 构建专利内容
    patent_sections = []
    if patent.title:
        patent_sections.append(f"## 发明名称\n{patent.title}")
    if patent.abstract:
        patent_sections.append(f"## 摘要\n{patent.abstract}")
    if patent.claims:
        patent_sections.append(f"## 权利要求书\n" + "\n".join(patent.claims))
    if patent.ipc_classifications:
        patent_sections.append(
            f"## IPC分类号\n" + ", ".join(patent.ipc_classifications))
    if patent.applicants:
        patent_sections.append(f"## 申请人\n" + ", ".join(patent.applicants))
    if patent.inventors:
        patent_sections.append(f"## 发明人\n" + ", ".join(patent.inventors))
    if patent.description:
        desc_short = patent.description[:2000]
        patent_sections.append(f"## 说明书（节选）\n{desc_short}...")

    patent_markdown = "\n\n".join(patent_sections)

    return f"""# 专利技术方案分析

{patent_markdown}

---

# 任务

你是一名专利审查员，需要检索和本申请相关的现有技术，以评述新颖性和创造性。

## 构建检索式的原则

### 原则1：先保证有结果
第一个检索式必须是 **宽泛检索**，只提取最核心的 2-3 个关键词，用 `/A` 字段搜索，
保证一定能搜到一定数量的结果。

### 原则2：关键词扩展
对核心技术词做充分扩展：同义词、上下位概念、等效手段，用 OR 连接。
例如：`(压力检测 OR 压力测量 OR 压力传感 OR 力传感器)/A`

### 原则3：一个角度一个检索式
每个检索式只做 **一个角度的检索**，不要在一句里塞太多条件。

示例角度：
- **角度1（必选）**：核心关键词宽泛检索 → `(关键词1 关键词2)/A`
- **角度2（必选）**：核心关键词 + IPC分类 → `(关键词1 关键词2)/A AND G06K9/IC`
- **角度3（可选）**：申请人相关专利 → `华为/PA`
- **角度4（可选）**：精准标题检索 → `(关键词1 关键词2)/TI`

### 原则4：语法越简单越好
- 用 `/A`（标题+摘要+权利要求）作为默认字段，别用 `/B`（全文太长）
- 不要用邻近算子（nW, nD, S, P）
- 不要用 NOT（容易误杀相关结果）
- 每个检索式的运算符（AND/OR）不超过 2 个

### 语法速查
- `/TI` 标题、`/AB` 摘要、`/A` 标题+摘要+权利要求、`/IC` IPC分类、`/PA` 申请人
- `AND`、`OR`、`""` 精确匹配、`*` 通配符

## 输出格式

纯JSON数组，不要markdown代码块包裹：

```json
[
  {{
    "query_string": "检索式",
    "search_angle": "角度说明",
    "rationale": "为什么这个检索式有效",
    "priority": 1
  }}
]
```

- 数组长度不超过 {max_queries} 个
- priority: 1 = 最宽泛/最重要，往后逐渐精准
- 第1个检索式必须是最简单、最宽泛、保证有结果的
"""
