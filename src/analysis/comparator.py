"""
专利对比分析器 - 使用AI API(DeepSeek/Kimi)进行专利对比分析
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.pdf_extractor.extractor import PatentDocument
from src.ai_client import AIClient


class PatentComparator:
    """专利对比分析引擎"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def compare_batch(self, patent: PatentDocument,
                      results: list[dict]) -> list[dict]:
        """批量对比分析（两阶段）"""
        client = self._get_client()
        comparisons = []

        # Pass 1: 批量相关度评分
        scored = self._batch_relevance_scoring(client, patent, results)

        # Pass 2: 对高相关度结果做详细对比
        top_n = self.settings.analysis_top_n
        top_results = sorted(scored, key=lambda x: x.get("relevance_score", 0),
                             reverse=True)[:top_n]

        for r in top_results:
            detail = self._detailed_comparison(client, patent, r)
            if detail:
                comparisons.append(detail)

        return comparisons

    def _batch_relevance_scoring(self, client: AIClient,
                                  patent: PatentDocument,
                                  results: list[dict]) -> list[dict]:
        """阶段1: 批量相关度评分"""
        result_summaries = []
        for i, r in enumerate(results, 1):
            result_summaries.append(
                f"[{i}] {r.get('publication_number', 'N/A')}: "
                f"{r.get('title', 'N/A')} | "
                f"{r.get('abstract', '')[:200]}..."
            )

        prompt = f"""# 本申请专利
标题: {patent.title}
摘要: {patent.abstract}
权利要求（核心）:
{'; '.join(patent.claims[:5])}

# 对比文献列表
{chr(10).join(result_summaries)}

请对以上每篇对比文献与本申请的相关度进行评分（0-100分）。
评分标准：
- 80-100: 高度相关，技术方案高度重叠
- 60-79: 中度相关，存在关键特征重叠
- 40-59: 低度相关，仅部分领域相似
- 0-39: 不相关或领域不同

请以JSON格式输出：{{"scores": [{{"index": 1, "score": 85, "reason": "..."}}, ...]}}
只输出JSON，不要包含其他内容。"""

        try:
            content = client.chat(
                system_prompt="你是一位专利审查分析师，负责评估对比文献与专利申请的相关度。",
                user_prompt=prompt,
                max_tokens=4096,
                temperature=0.3,
            )
            scores = self._parse_scores(content)

            for score_entry in scores:
                idx = score_entry.get("index", 0) - 1
                if 0 <= idx < len(results):
                    results[idx]["relevance_score"] = score_entry.get("score", 0)
                    results[idx]["score_reason"] = score_entry.get("reason", "")

        except Exception as e:
            print(f"批量评分失败: {e}")

        return results

    def _detailed_comparison(self, client: AIClient,
                              patent: PatentDocument,
                              result: dict) -> Optional[dict]:
        """阶段2: 对单个结果做详细对比（使用完整全文）"""

        # 候选专利：完整全文，不截断
        cand_claims = result.get("claims") or ""
        cand_desc = result.get("description") or ""
        cand_abstract = result.get("abstract") or result.get("abstract_snippet") or ""

        prompt = f"""# 本申请专利
发明名称: {patent.title}
IPC分类: {', '.join(patent.ipc_classifications)}
摘要: {patent.abstract}

权利要求书:
{chr(10).join(patent.claims[:10])}

说明书（节选）:
{patent.description[:1500] if patent.description else '(无)'}

# 对比文献
公布号: {result.get('publication_number', 'N/A')}
标题: {result.get('title', 'N/A')}
申请人: {result.get('applicant', 'N/A')}
公开日: {result.get('publication_date', 'N/A')}
IPC: {result.get('ipc', 'N/A')}
摘要: {cand_abstract}

权利要求书:
{cand_claims if cand_claims else '(无)'}

说明书（节选）:
{cand_desc if cand_desc else '(无)'}

# 分析任务
请对以上对比文献与本申请进行专业对比分析，输出JSON格式：

{{
  "publication_number": "对比文献公布号",
  "relevance_score": 0-100的评分,
  "novelty_impact": "新颖性影响: high/moderate/low",
  "inventive_step_impact": "创造性影响: high/moderate/low",
  "key_features_same": ["与本申请相同的技术特征列表"],
  "key_features_different": ["与本申请不同的技术特征列表"],
  "conclusion": "综合评述（100-200字）"
}}

只输出JSON，不要包含其他内容。"""

        try:
            content = client.chat(
                system_prompt="你是一位中国专利审查专家，精通新颖性和创造性判断。",
                user_prompt=prompt,
                max_tokens=4096,
                temperature=0.3,
            )
            detail = self._parse_detail(content)
            if detail:
                detail["source_raw"] = result
                return detail
        except Exception as e:
            print(f"详细对比失败 ({result.get('publication_number', '')}): {e}")

        return None

    def _parse_scores(self, content: str) -> list[dict]:
        try:
            content = self._clean_json(content)
            data = json.loads(content)
            return data.get("scores", [])
        except json.JSONDecodeError:
            return []

    def _parse_detail(self, content: str) -> Optional[dict]:
        try:
            content = self._clean_json(content)
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def compare_single_fulltext(self, patent_doc,
                                  candidate: dict) -> dict:
        """
        单篇全⽂对比 — ⽤于点击左侧专利后在右侧显示详细分析。

        Returns:
            dict with analysis markdown and structured data
        """
        client = self._get_client()
        pub = candidate.get("publication_number", "?")
        title = candidate.get("title", "")
        claims = candidate.get("claims", "") or ""
        description = candidate.get("description", "") or ""
        abstract = candidate.get("abstract", "") or ""
        ipc = candidate.get("ipc", "")
        applicant = candidate.get("applicant", "")
        pub_date = candidate.get("publication_date", "")
        fulltext_score = candidate.get("fulltext_score", "")

        comparison_prompt = f"""# 本申请专利
**发明名称**: {patent_doc.title}
**IPC分类**: {', '.join(patent_doc.ipc_classifications)}
**摘要**: {patent_doc.abstract}

**权利要求书（核心）**:
{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(patent_doc.claims[:5]))}

# 对比文献
**公布号**: {pub}
**标题**: {title}
**申请人**: {applicant}
**公开日**: {pub_date}
**IPC**: {ipc}
**AI相关度评分**: {fulltext_score}/100

**摘要**:
{abstract}

**权利要求书**:
{claims[:3000]}

**说明书（关键部分）**:
{description[:3000]}

---
请以中国专利审查员的视角，对这篇对比文献与本申请进行详细对比分析。

按以下 Markdown 格式输出：

## 对比分析: {pub} vs 本申请

### 1. 基本信息对比
| 项目 | 本申请 | 对比文献 |
|------|--------|----------|
| 标题 | {patent_doc.title} | {title} |
| IPC | {', '.join(patent_doc.ipc_classifications[:3])} | {ipc} |
| 申请人 | - | {applicant} |
| 公开日 | - | {pub_date} |

### 2. 技术领域分析
（100-200字，分析两者是否属于相同或相近技术领域）

### 3. 权利要求对比
（列出两者权利要求的相同点和不同点）

### 4. 新颖性分析
（分析对比文献是否影响本申请的新颖性，指出具体哪些权利要求可能被公开）

### 5. 创造性分析（三步法）
（如果对比文献可作为最接近的现有技术，分析本申请权利要求的创造性）
- 区别技术特征
- 实际解决的技术问题
- 是否显而易见

### 6. 综合结论
（100-200字，总结这篇对比文献的参考价值）"""

        try:
            content = client.chat(
                system_prompt="你是一位资深中国专利审查员，精通专利法第22条（新颖性）和第23条（创造性）的三步法分析。请提供专业、准确、有深度的对比分析。",
                user_prompt=comparison_prompt,
                max_tokens=4096,
                temperature=0.3,
            )
            return {"markdown": content, "publication_number": pub}
        except Exception as e:
            return {"markdown": f"# 对比分析失败\n\n{pub}: {e}", "publication_number": pub}

    def _clean_json(self, content: str) -> str:
        content = content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        while content and content[0] not in "[{":
            content = content[1:]
        while content and content[-1] not in "]}":
            content = content[:-1]
        return content
