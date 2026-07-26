"""
AI 对比文件粗筛模块 — 从大量摘要中筛选出最相关的 10-20 篇
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient


SCREENING_SYSTEM_PROMPT = """你是一名中国专利审查员。你需要从一批检索结果中，快速筛选出与本申请最相关、最可能用于评述新颖性或创造性的对比文件。

## 筛选标准（重要：宁可多留，不可漏掉）

对每篇对比文件，综合考虑：
1. **技术领域相关性（权重最高）**：是否与本申请属于同一或相近技术领域
   - 相同 IPC 大类 或 相同材料/器件体系（如IGZO、GaN、SiC等）的专利，即使摘要关键词不完全匹配，**至少给 60 分**
   - 同领域专利可能是最接近的现有技术，不应因摘要措辞差异被筛掉
2. **技术特征重叠度**：摘要中描述的技术方案与本申请的核心发明点有多少重合
3. **预期可用性**：是否可能作为最接近的现有技术（用于三步法分析）

## 评分指导
- 相同 IPC 大类 + 相同器件类型（如都是IGZO TFT）：≥70分
- 相同 IPC 大类 + 相关器件类型：≥60分
- 不同 IPC 但技术方案相关：50-70分
- 完全不相关：<40分

## 输出格式

纯 JSON 数组，按相关度从高到低排序，最多输出 {top_n} 篇：

```json
[
  {{
    "doc_id": "专利号",
    "title": "专利标题",
    "relevance_score": 95,
    "relevance_reason": "为什么相关（一句话）",
    "suggested_use": "novelty" 或 "inventive_step" 或 "background"
  }}
]
```

- relevance_score: 0-100, 高于 70 才值得看全文
- suggested_use: novelty=可用于新颖性评述, inventive_step=创造性三步法, background=背景技术"""


class PatentScreener:
    """AI 专利粗筛器"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def screen(self, patent_doc, abstracts: list[dict],
               top_n: int = 15, max_batch: int = 40) -> list[dict]:
        """分层筛选：先粗筛（小token）再基于全文打分。批量超过max_batch时分批。"""
        if not abstracts:
            return []

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(abstracts)

        # 分批处理（每批最多 max_batch 篇）
        all_scored = []
        batch_size = min(max_batch, total)
        num_batches = (total + batch_size - 1) // batch_size

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batch = abstracts[start:end]

            if num_batches > 1:
                # 分批时，每批独立筛选 top_n 篇
                batch_top_n = top_n
            else:
                batch_top_n = top_n

            candidates_text = self._build_candidates(batch)

            user_prompt = f"""## 本申请

{patent_summary}

## 检索结果 第{batch_idx+1}/{num_batches}批（共{batch_top_n}篇任务）

{candidates_text}

---

请筛选出与本申请最相关的对比文件。"""

            system_prompt = SCREENING_SYSTEM_PROMPT.replace("{top_n}", str(batch_top_n))

            response = client.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4096,
                temperature=0.3,
                json_mode=True,
            )

            scored = self._parse_response(response, batch)
            all_scored.extend(scored)

        # 全部批次合并，按相关度降序取 top_n
        all_scored.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return all_scored[:top_n]

    def _build_patent_summary(self, patent_doc) -> str:
        """构建本申请的技术方案摘要"""
        parts = []
        if patent_doc.title:
            parts.append(f"**发明名称**: {patent_doc.title}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC分类**: {', '.join(patent_doc.ipc_classifications)}")
        if patent_doc.abstract:
            parts.append(f"**摘要**: {patent_doc.abstract}")
        if patent_doc.claims:
            # 只取前3项权利要求
            parts.append(f"**核心权利要求**:\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(patent_doc.claims[:3])
            ))
        return "\n\n".join(parts)

    def _build_candidates(self, abstracts: list[dict]) -> str:
        """构建候选文件列表文本"""
        lines = []
        for i, a in enumerate(abstracts):
            pub = a.get("publication_number", "?")
            title = a.get("title", "无标题")
            abs_text = a.get("abstract_snippet", "")[:300]
            ipc = a.get("ipc", "")
            applicant = a.get("applicant", "")

            lines.append(
                f"### [{i+1}] {pub}\n"
                f"- 标题: {title}\n"
                f"- IPC: {ipc}\n"
                f"- 申请人: {applicant}\n"
                f"- 摘要: {abs_text}\n"
            )
        return "\n".join(lines)

    def _parse_response(self, response: str, abstracts: list[dict]) -> list[dict]:
        """解析 AI 返回的筛选结果，映射回原始数据"""
        # 提取 JSON
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])

        try:
            selected = json.loads(json_str)
        except json.JSONDecodeError:
            import re
            match = re.search(r"\[[\s\S]*\]", response, re.DOTALL)
            if match:
                try:
                    selected = json.loads(match.group(0))
                except json.JSONDecodeError:
                    # 打印 AI 原始返回以便排查
                    import sys
                    print(f"[Screener] JSON parse failed. Raw response (first 500): {response[:500]}", file=sys.stderr)
                    return []
            else:
                import sys
                print(f"[Screener] No JSON array found. Raw response (first 500): {response[:500]}", file=sys.stderr)
                return []

        if not isinstance(selected, list):
            return []

        # 构建 doc_id → abstract 的索引
        idx = {}
        for a in abstracts:
            doc_id = a.get("doc_id", "")
            pub_num = a.get("publication_number", "")
            idx[doc_id] = a
            idx[pub_num] = a

        result = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            doc_id = item.get("doc_id", "")
            # 用 doc_id 匹配原始数据
            matched = idx.get(doc_id)
            if not matched:
                # 模糊匹配
                for key in idx:
                    if doc_id and doc_id in key:
                        matched = idx[key]
                        break

            if matched:
                result.append({
                    **matched,
                    "relevance_score": item.get("relevance_score", 0),
                    "relevance_reason": item.get("relevance_reason", ""),
                    "suggested_use": item.get("suggested_use", ""),
                })

        # 按相关度降序
        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        return result[:self.settings.analysis_top_n]

    FULLTEXT_SCORING_PROMPT = """你是一台评分机器。请通读对比文件全文，评估与本申请的相关度。

评分标准 (0-100):
- 技术领域 0-25
- 技术问题 0-25
- 技术方案重叠度 0-25
- 证据强度 0-25

你必须只输出一行JSON，不要任何解释、不要markdown、不要换行：
{"relevance_score": 85, "relevance_reason": "一句话原因"}"""

    def score_full_text(self, patent_doc, enriched_patents: list[dict],
                        signals=None) -> list[dict]:
        """
        逐篇通读全文，给出 0-100 相关度评分。

        Args:
            patent_doc: 本申请
            enriched_patents: Phase 3 获取全文后的专利列表（含 claims, description）

        Returns:
            附 fulltext_score 和 fulltext_reason 的专利列表
        """
        if not enriched_patents:
            return []

        import re as re_module
        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(enriched_patents)

        for i, p in enumerate(enriched_patents):
            pub = p.get("publication_number", "?")
            title = p.get("title", "")
            claims = (p.get("claims") or "")[:5000]
            description = (p.get("description") or "")[:5000]
            abstract = (p.get("abstract") or "")[:2000]
            # 如果结构化字段为空，从 full_text 中提取
            ft = p.get("full_text") or ""
            if not claims.strip() and ft:
                m = re_module.search(r'(Claims|权利要求书)[\s\S]*?(?=Description|说明书|$)', ft)
                claims = (m.group(0) if m else ft)[:5000]
            if not description.strip() and ft:
                m = re_module.search(r'(Description|说明书)[\s\S]*?(?=Claims|权利要求|$)', ft)
                description = (m.group(0) if m else ft)[:5000]
            if not abstract.strip() and ft:
                m = re_module.search(r'(Abstract|摘要)[\s\S]*?(?=Claims|权利要求|$)', ft)
                abstract = (m.group(0) if m else ft)[:2000]

            full_text = f"""## 对比文件 [{i+1}/{total}]

**公开号**: {pub}
**标题**: {title}
**摘要**: {abstract}

**权利要求**:
{claims if claims else '(无)'}

**说明书摘要**:
{description if description else '(无)'}"""

            if signals:
                signals.log.emit("INFO",
                    f"  AI 通读全文 [{i+1}/{total}]: {pub} ...")

            try:
                response = client.chat(
                    system_prompt=self.FULLTEXT_SCORING_PROMPT,
                    user_prompt=f"## 本申请\n\n{patent_summary}\n\n{full_text}\n\n请输出JSON评分。",
                    max_tokens=512,
                    temperature=0.2,
                    json_mode=False,  # flash 模型 json_mode 不稳定，用降级提取
                )
                # 解析 JSON（处理 markdown 代码块、多行等变体）
                import json as json_module
                import re as re_module
                resp = response.strip()
                # 先去掉 markdown 代码块
                if resp.startswith("```"):
                    resp = re_module.sub(r'^```\w*\n?', '', resp)
                    resp = re_module.sub(r'\n?```$', '', resp)
                # 匹配 JSON 对象（支持嵌套）
                match = re_module.search(r'\{[\s\S]*?\}', resp)
                if match:
                    try:
                        data = json_module.loads(match.group(0))
                        p["fulltext_score"] = data.get("relevance_score", 0)
                        p["fulltext_reason"] = data.get("relevance_reason", "")
                        continue  # 成功，跳过降级
                    except json_module.JSONDecodeError:
                        pass  # JSON 坏了，继续尝试降级

                # 降级1：从任意文本中提取数字分数
                score_match = re_module.search(r'(?:relevance|score|相关度|评分)\D*(\d{1,3})', resp, re_module.IGNORECASE)
                if not score_match:
                    score_match = re_module.search(r'\b(\d{2,3})\b', resp)  # 2-3位数字
                if score_match:
                    p["fulltext_score"] = int(score_match.group(1))
                    p["fulltext_reason"] = resp[:120].replace('\n', ' ')
                    continue

                # 降级2：不是JSON也不是数字，用摘要分数兜底
                if len(resp) > 20:
                    p["fulltext_score"] = max(30, int(p.get("relevance_score", 0) or 0))
                    p["fulltext_reason"] = resp[:120].replace('\n', ' ')
                else:
                    # AI返回空，保留摘要阶段分数
                    p["fulltext_score"] = p.get("relevance_score", 30)
                    p["fulltext_reason"] = f"AI未响应，保留摘要评分({p['fulltext_score']})"
            except Exception as e:
                p["fulltext_score"] = 0
                p["fulltext_reason"] = f"评分异常: {str(e)[:40]}"
                if signals:
                    signals.log.emit("WARN",
                        f"  {pub} 全文评分失败: {e}")

            if signals:
                score = p.get("fulltext_score", 0)
                reason = p.get("fulltext_reason", "")[:60]
                signals.log.emit("INFO",
                    f"  [{i+1}/{total}] {pub}: 相关度 {score}分 - {reason}")

        # 按全文评分的相关度降序
        enriched_patents.sort(
            key=lambda x: x.get("fulltext_score", 0), reverse=True)

        return enriched_patents
