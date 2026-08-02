"""
多阶段 LLM 提示词统一加载与渲染工具。

各业务阶段（Claims 广筛、新颖性/创造性对比、终选评述、
检索式生成、OA 撰写）的提示词均存放在
config/prompts/<stage>/{system,user}.txt。本模块提供：
  - load_prompt():       读文件，缺失回退到代码内兜底常量
  - render_template():   按 {name} 占位符安全替换（未声明的花括号原样保留）

兜底常量全部集中在本模块，供业务代码与 GUI 提示词编辑器共用。
本模块不 import 任何应用模块，避免循环依赖。
"""
from typing import Any


def load_prompt(settings, stage: str, prompt_type: str,
                fallback: str = "") -> str:
    """读取指定阶段的提示词文本。

    Args:
        settings: Settings 实例（提供 get_prompt_text）
        stage: 阶段/profile 文件夹名，如 "screen_claims"
        prompt_type: 文件名主干，如 "system" / "user" / "system_claims"
        fallback: 文件缺失时返回的兜底文本
    Returns:
        prompt 文本；文件不存在时返回 fallback
    """
    return settings.get_prompt_text(stage, prompt_type) or fallback


def render_template(template: str, **kwargs: Any) -> str:
    """按 {name} 占位符安全替换模板变量。

    只替换已知键对应的 {name} 占位符；未声明的花括号（如 user 模板里
    JSON 输出示例的字面量 {}）原样保留，避免 str.format() 要求转义
    {{}} 而用户在 GUI 手改时漏转义抛 KeyError。

    Args:
        template: 提示词模板文本
        kwargs: {name: value} 变量值
    Returns:
        替换后的文本
    """
    if not template:
        return template
    for key, value in kwargs.items():
        template = template.replace("{%s}" % key, str(value))
    return template


# ================================================================
# Claims 广筛（screen_claims）— 按内容模式三变体，只发权利要求/实施方式
# ================================================================

SCREEN_CLAIMS_FALLBACK_SYSTEM_PROMPTS = {
    "claims": """你是一名中国专利审查员。你需要从一批对比文件中，筛选出与本申请最相关的专利。

你现在看到的是每篇候选专利的**权利要求书**（可能只截取了开头部分，保留独立权利要求和部分从属权利要求），据此判断技术方案相关性。

## 筛选标准
1. **技术领域相关性（权重最高）**：相同 IPC 大类或相同材料/器件体系的专利优先
2. **技术特征重叠度**：权利要求中描述的技术方案与本申请核心发明点的重合程度
3. **预期可用性**：是否可能作为最接近的现有技术用于评述新颖性或创造性

## 评分指导
- 相同 IPC + 相同器件/材料体系 + 技术方案高度重叠：≥85分
- 相同 IPC + 相关技术领域 + 部分特征重叠：70-84分
- 不同 IPC 但技术方案相关：55-69分
- 技术领域接近但方案差异大：40-54分
- 完全不相关：<40分

## 输出格式
纯 JSON 数组，按相关度降序，必须包含全部 {total} 篇：
```json
[{"publication_number":"公布号","relevance_score":85,"relevance_reason":"一句话原因","key_features":["关键技术特征1","特征2"]}]
```""",
    "embodiments": """你是一名中国专利审查员。你需要从一批对比文件中，筛选出与本申请最相关的专利。

你现在看到的是每篇候选专利的**具体实施方式**（实施例/详述，可能只截取了开头部分）。具体实施方式公开了对比文件实际记载的实施方案，是判断其能否作为现有技术评述本申请新颖性/创造性的关键。

## 筛选标准
1. **技术领域相关性（权重最高）**：相同 IPC 大类或相同材料/器件体系的专利优先
2. **实施方案重叠度**：具体实施方式中记载的技术方案与本申请核心发明点的重合程度（是否公开了本申请实际要解决的技术问题及其解决手段）
3. **预期可用性**：是否可能作为最接近的现有技术用于评述新颖性或创造性

## 评分指导
- 相同 IPC + 相同器件/材料体系 + 实施方案高度重叠：≥85分
- 相同 IPC + 相关技术领域 + 部分特征重叠：70-84分
- 不同 IPC 但技术方案相关：55-69分
- 技术领域接近但方案差异大：40-54分
- 完全不相关：<40分

## 输出格式
纯 JSON 数组，按相关度降序，必须包含全部 {total} 篇：
```json
[{"publication_number":"公布号","relevance_score":85,"relevance_reason":"一句话原因","key_features":["关键技术特征1","特征2"]}]
```""",
    "both": """你是一名中国专利审查员。你需要从一批对比文件中，筛选出与本申请最相关的专利。

你现在看到的是每篇候选专利的**权利要求书 + 具体实施方式**（实施例/详述，可能截取开头部分）。权利要求界定保护范围，具体实施方式公开实际实施方案，两者结合判断其能否作为现有技术评述本申请。

## 筛选标准
1. **技术领域相关性（权重最高）**：相同 IPC 大类或相同材料/器件体系的专利优先
2. **技术特征/实施方案重叠度**：权利要求与具体实施方式中记载的技术方案，与本申请核心发明点的重合程度
3. **预期可用性**：是否可能作为最接近的现有技术用于评述新颖性或创造性

## 评分指导
- 相同 IPC + 相同器件/材料体系 + 方案高度重叠：≥85分
- 相同 IPC + 相关技术领域 + 部分特征重叠：70-84分
- 不同 IPC 但技术方案相关：55-69分
- 技术领域接近但方案差异大：40-54分
- 完全不相关：<40分

## 输出格式
纯 JSON 数组，按相关度降序，必须包含全部 {total} 篇：
```json
[{"publication_number":"公布号","relevance_score":85,"relevance_reason":"一句话原因","key_features":["关键技术特征1","特征2"]}]
```""",
}

SCREEN_CLAIMS_FALLBACK_USER_PROMPT = """## 本申请
{patent_summary}

## 对比文件 第{batch_number}/{num_batches}批（共{batch_count}篇）
{candidates_text}

---
请给每篇评分。输出 JSON 数组，必须包含全部 {batch_count} 篇。"""


# ================================================================
# 新颖性/创造性详细对比（comparison）— 单篇对比输出结构化结论
# ================================================================

COMPARISON_FALLBACK_SYSTEM_PROMPT = "你是一位中国专利审查专家，精通新颖性和创造性判断。"

COMPARISON_FALLBACK_USER_PROMPT = """# 本申请专利
发明名称: {application_title}
IPC分类: {application_ipc}
摘要: {application_abstract}

权利要求书:
{application_claims}

说明书（节选）:
{application_description}

# 对比文献
公布号: {comparison_publication_number}
标题: {comparison_title}
申请人: {comparison_applicant}
公开日: {comparison_publication_date}
IPC: {comparison_ipc}
摘要: {comparison_abstract}

权利要求书:
{comparison_claims}

说明书（节选）:
{comparison_description}

# 分析任务
请对以上对比文献与本申请进行专业对比分析，输出JSON格式：

{
  "publication_number": "对比文献公布号",
  "relevance_score": 0-100的评分,
  "novelty_impact": "新颖性影响: high/moderate/low",
  "inventive_step_impact": "创造性影响: high/moderate/low",
  "key_features_same": ["与本申请相同的技术特征列表"],
  "key_features_different": ["与本申请不同的技术特征列表"],
  "conclusion": "综合评述（100-200字）"
}

只输出JSON，不要包含其他内容。"""


# ================================================================
# 终选评述（final_review）— 从历史最佳候选池挑最终对比文件
# ================================================================

FINAL_REVIEW_FALLBACK_SYSTEM_PROMPT = ("你是中国专利审查员。根据候选池中每篇的评分、理由和关键特征，"
                                       "从历史最佳中挑选最终用于评述的对比文件。"
                                       "只输出公布号 JSON 数组，不要其他内容。")

FINAL_REVIEW_FALLBACK_USER_PROMPT = """## 本申请
{patent_summary}

## 候选池（{pool_size} 篇，按历史最佳评分排序）
{pool_text}

---
从以上候选池中，选出最合适作为**最接近的现有技术**的恰好 {final_n} 篇。
考虑：
- 新颖性评述：单篇公开了最多与本申请相同的技术特征
- 创造性评述：需要至少 1 篇最接近 + 若干篇可组合公开区别特征
只输出公布号 JSON 数组，不要评分不要理由：
```json
["CN117317030B", "WO2019006821A1", ...]
```"""


# ================================================================
# 各阶段兜底汇总 — GUI 提示词编辑器恢复默认也从这里取
# ================================================================

STAGE_FALLBACKS = {
    "screen_claims": {
        "system": SCREEN_CLAIMS_FALLBACK_SYSTEM_PROMPTS,  # dict by mode
        "user": SCREEN_CLAIMS_FALLBACK_USER_PROMPT,
    },
    "comparison": {
        "system": COMPARISON_FALLBACK_SYSTEM_PROMPT,
        "user": COMPARISON_FALLBACK_USER_PROMPT,
    },
    "final_review": {
        "system": FINAL_REVIEW_FALLBACK_SYSTEM_PROMPT,
        "user": FINAL_REVIEW_FALLBACK_USER_PROMPT,
    },
}
