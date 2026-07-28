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
        """批量对比分析：直接用已有评分排序，对 Top-N 做详细对比。

        评分来源（上游已计算，不再重复调 AI）：
          - fulltext_score（screen_fulltext / 阶段4）
          - 降级用 relevance_score
        """
        client = self._get_client()
        comparisons = []

        # 按已有评分排序（上游 screen_fulltext 已算好，不重复调 AI）
        top_n = self.settings.analysis_top_n
        top_results = sorted(results,
            key=lambda x: x.get("fulltext_score", x.get("relevance_score", 0)),
            reverse=True)[:top_n]

        for r in top_results:
            detail = self._detailed_comparison(client, patent, r)
            if detail:
                comparisons.append(detail)

        return comparisons

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
                model=self.settings.ai_analysis_model,
            )
            detail = self._parse_detail(content)
            if detail:
                detail["source_raw"] = result
                return detail
        except Exception as e:
            print(f"详细对比失败 ({result.get('publication_number', '')}): {e}")

        return None

    def _parse_detail(self, content: str) -> Optional[dict]:
        try:
            content = self._clean_json(content)
            return json.loads(content)
        except json.JSONDecodeError:
            return None

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
