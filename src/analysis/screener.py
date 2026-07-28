"""
AI 对比文件粗筛模块 — 分层筛选 + 批量全文评分
"""
import json as json_module
import re as re_module
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient


FULLTEXT_SCREENING_PROMPT = """你是一名中国专利审查员。你需要从一批对比文件中，筛选出与本申请最相关的专利。

你现在看到的是每篇专利的**完整权利要求和说明书**，信息充分，不需要猜测。

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
[{"publication_number":"公布号","relevance_score":85,"relevance_reason":"一句话原因"}]
```"""


class PatentScreener:
    """AI 专利筛选器 — 支持摘要粗筛和全文精选"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    # ================================================================
    # 快速粗筛（一批搞定，只选不评）
    # ================================================================

    def quick_screen(self, patent_doc, abstracts: list[dict],
                     top_n: int = 200, signals=None,
                     abstract_override: str = None) -> list[dict]:
        """快速粗筛：全部候选一批发给 AI，只返回 Top N 公布号。

        每篇只发 title + IPC + 短摘要（~150字），输入小。
        AI 只输出公布号列表，不评分，输出小。
        一批搞定，~30 秒。
        """
        if not abstracts or len(abstracts) <= top_n:
            return abstracts

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc,
            abstract_override=abstract_override)
        total = len(abstracts)

        if signals:
            signals.log.emit("INFO",
                f"  AI 快速粗筛: {total} 篇 → {top_n} 篇（一批搞定）")

        # 构建候选列表：安全兜底各1000字，防止抓取异常垃圾数据
        candidates_lines = []
        for i, a in enumerate(abstracts):
            pub = a.get("publication_number", "?")
            title = a.get("title", "")[:1000]
            ipc = a.get("ipc", "")
            snippet = (a.get("abstract_snippet") or "")[:1000]
            candidates_lines.append(
                f"[{i+1}] {pub} | IPC:{ipc}\n  标题: {title}\n  摘要: {snippet}\n")

        candidates_text = "\n".join(candidates_lines)

        user_prompt = f"""## 本申请
{patent_summary}

## 检索结果（共{total}篇，每行格式: [序号] 公布号 | IPC | 标题 | 摘要片段）
{candidates_text}

---
从以上 {total} 篇中选出与本申请最相关的 **恰好 {top_n} 篇**。
只输出公布号 JSON 数组，不要评分，不要理由：
```json
["CN117317030B", "WO2019006821A1", ...]
```"""

        system_prompt = """你是中国专利审查员。根据本申请的技术方案，从候选列表中选出最相关的专利。
只看技术领域和发明点是否相关，不要因为摘要片段短就漏掉。
只输出公布号 JSON 数组，不要其他内容。"""

        response = client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096, temperature=0.3,
            model=self.settings.ai_screen_model)

        # 解析公布号列表
        selected_pubs = self._parse_pub_list(response)
        if signals:
            signals.log.emit("INFO", f"  AI 返回 {len(selected_pubs)} 个公布号")

        # 匹配回原始数据
        pub_map = {}
        for a in abstracts:
            pub_map[a.get("publication_number", "")] = a
            # 也尝试用 doc_id 匹配
            did = a.get("doc_id", "")
            if did:
                pub_map[did] = a

        result = []
        for pub in selected_pubs:
            matched = pub_map.get(pub)
            if not matched:
                # 模糊匹配
                for k in pub_map:
                    if pub and k and (pub in k or k in pub):
                        matched = pub_map[k]
                        break
            if matched:
                matched["relevance_score"] = 70  # 默认分
                matched["relevance_reason"] = "AI 快速粗筛"
                result.append(matched)

        # 如果 AI 返回不够，用 PATENTSCOPE 原始排序补足
        if len(result) < top_n:
            for a in abstracts:
                if a not in result:
                    a["relevance_score"] = 50
                    a["relevance_reason"] = "原始排序补充"
                    result.append(a)
                    if len(result) >= top_n:
                        break

        if signals:
            signals.log.emit("SUCCESS",
                f"  快速粗筛完成: {len(result)} 篇")

        return result[:top_n]

    def _parse_pub_list(self, response: str) -> list[str]:
        """从 AI 响应中提取公布号列表"""
        import re as re_module
        resp = response.strip()
        # 去掉 markdown 代码块
        if resp.startswith("```"):
            resp = re_module.sub(r'^```\w*\n?', '', resp)
            resp = re_module.sub(r'\n?```$', '', resp)
        try:
            data = json_module.loads(resp)
            if isinstance(data, list):
                return [str(x) for x in data if x]
        except json_module.JSONDecodeError:
            pass
        # 尝试提取引号内的字符串
        matches = re_module.findall(r'"([^"]+)"', resp)
        if matches:
            return matches
        return []

    # ================================================================
    # 全文精选（加载磁盘上的完整详情，AI 精选 Top N）
    # ================================================================

    def screen_fulltext(self, patent_doc, details_dir: str,
                        batch_size: int = 30,
                        signals=None) -> list[dict]:
        """从磁盘加载完整专利详情，AI 全文评分排序。

        Args:
            patent_doc: 本申请 PatentDocument
            details_dir: 存放独立 JSON 的目录路径
            batch_size: 每批发给 AI 的篇数
            signals: WorkerSignals（用于日志和进度）

        Returns:
            按 fulltext_score 降序排列的全部专利列表
        """
        import glob as glob_module
        from pathlib import Path

        detail_path = Path(details_dir)
        if not detail_path.exists():
            return []

        # 加载所有成功抓取的专利
        patents = []
        for f in sorted(detail_path.glob("*.json")):
            try:
                d = json_module.loads(f.read_text(encoding="utf-8"))
                if d.get("fetch_status") == "ok" and d.get("claims"):
                    patents.append(d)
            except Exception:
                pass

        if not patents:
            return []

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(patents)

        if signals:
            signals.log.emit("INFO",
                f"  AI 全文精选: 加载 {total} 篇完整详情, "
                f"分批 {batch_size} 篇/批")

        all_scored = []
        num_batches = (total + batch_size - 1) // batch_size

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batch = patents[start:end]

            if signals:
                signals.progress.emit(
                    50 + int((batch_idx + 1) / num_batches * 20),
                    f"AI 全文精选 第{batch_idx+1}/{num_batches}批")

            # 构建候选文本：每篇截断到合理长度（可配置）
            max_chars = self.settings.get("analysis", "max_chars_per_patent", default=9000)
            # 分配：claims 占 55%，description 占 35%，abstract 占 10%
            claim_limit = int(max_chars * 0.55)
            desc_limit = int(max_chars * 0.35)
            abs_limit = int(max_chars * 0.10)

            candidates_lines = []
            for i, p in enumerate(batch):
                pub = p.get("publication_number", "?")
                title = p.get("title", "")
                ipc = p.get("ipc", "")
                applicant = p.get("applicant", "")
                claims = (p.get("claims") or "")[:claim_limit]
                description = (p.get("description") or "")[:desc_limit]
                abstract = (p.get("abstract") or "")[:abs_limit]

                candidates_lines.append(
                    f"### [{start+i+1}] {pub}\n"
                    f"- 标题: {title}\n"
                    f"- IPC: {ipc}\n"
                    f"- 申请人: {applicant}\n"
                    f"- 摘要: {abstract}\n"
                    f"- 权利要求:\n{claims}\n"
                    f"- 说明书:\n{description}\n"
                )

            candidates_text = "\n".join(candidates_lines)

            user_prompt = f"""## 本申请
{patent_summary}

## 对比文件 第{batch_idx+1}/{num_batches}批（共{len(batch)}篇）
{candidates_text}

---
请给每篇评分。输出 JSON 数组，必须包含全部 {len(batch)} 篇。"""

            system_prompt = FULLTEXT_SCREENING_PROMPT.replace(
                "{total}", str(len(batch)))

            if signals:
                signals.log.emit("INFO",
                    f"  发送第{batch_idx+1}批 ({len(batch)}篇) 给 AI 评分...")

            try:
                response = client.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=8192, temperature=0.2,
                    model=self.settings.ai_screen_model)

                scored = self._parse_response(response, batch)
                all_scored.extend(scored)
            except Exception as e:
                if signals:
                    signals.log.emit("ERROR",
                        f"  第{batch_idx+1}批评分失败: {e}")
                # 失败时保留原始数据，给默认分
                for p in batch:
                    p["fulltext_score"] = p.get("relevance_score", 30)
                    p["fulltext_reason"] = f"评分异常: {str(e)[:40]}"
                    all_scored.append(p)

        # 按分数降序排列
        all_scored.sort(
            key=lambda x: x.get("fulltext_score", x.get("relevance_score", 0)),
            reverse=True)

        if signals:
            signals.log.emit("SUCCESS",
                f"  AI 全文精选完成: {len(all_scored)} 篇")

        return all_scored

    # ================================================================
    # 内部方法
    # ================================================================

    def _build_patent_summary(self, patent_doc, abstract_override: str = None) -> str:
        parts = []
        if patent_doc.title:
            parts.append(f"**发明名称**: {patent_doc.title}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC分类**: {', '.join(patent_doc.ipc_classifications)}")
        abstract = abstract_override or patent_doc.abstract
        if abstract:
            parts.append(f"**摘要**: {abstract}")
        if patent_doc.claims:
            parts.append(f"**权利要求** ({len(patent_doc.claims)}项):\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(patent_doc.claims)))
        return "\n\n".join(parts)

    def _parse_response(self, response: str, abstracts: list[dict]) -> list[dict]:
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])
        try:
            selected = json_module.loads(json_str)
        except json_module.JSONDecodeError:
            match = re_module.search(r"\[[\s\S]*\]", response, re_module.DOTALL)
            if match:
                try:
                    selected = json_module.loads(match.group(0))
                except json_module.JSONDecodeError:
                    return []
            else:
                return []
        if not isinstance(selected, list):
            return []

        idx = {}
        for a in abstracts:
            idx[a.get("publication_number", "")] = a
            idx[a.get("publication_number", "")] = a

        result = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            publication_number = item.get("publication_number", "")
            matched = idx.get(publication_number)
            if not matched:
                for key in idx:
                    if publication_number and publication_number in key:
                        matched = idx[key]
                        break
            if matched:
                result.append({**matched, "relevance_score": item.get("relevance_score", 0),
                               "relevance_reason": item.get("relevance_reason", ""),
                               "suggested_use": item.get("suggested_use", "")})
        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return result
