"""
AI 对比文件筛选模块 — Claims 广筛（分批批量评分）
"""
import json as json_module
import re as re_module
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient
from src.utils.patent_extract import extract_embodiments
from src.utils.prompts import (
    load_prompt,
    render_template,
    SCREEN_CLAIMS_FALLBACK_SYSTEM_PROMPTS,
    SCREEN_CLAIMS_FALLBACK_USER_PROMPT,
)


# screen_claims 内容模式 → system 文件主干
_SCREEN_CLAIMS_MODE_FILE = {
    "claims": "claims",
    "embodiments": "embodiments",
    "claims+embodiments": "both",
}


class PatentScreener:
    """AI 专利筛选器 — 支持 Claims 广筛（只发权要/实施方式，分批评分）"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def _parse_pub_list(self, response: str) -> list[str]:
        """从 AI 响应中提取公布号列表（终选评述复用）"""
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
    # 全量 Claims 广筛（只发权利要求书，按字符预算自适应分批）
    # ================================================================

    def screen_claims_all(self, patent_doc, details_dir: str,
                          signals=None, log_dir: str | None = None,
                          concurrency: int | None = None,
                          only_pubs: set | None = None) -> list[dict]:
        """从磁盘加载完整详情，只发权利要求/实施方式分批广筛，全部评分排序。

        设计要点：
          - 每篇只发 claims/实施方式（截断到 screen_claims_limit），不发说明书，
            单篇信息量更大且总量可控，1000 篇也能在 1M 上下文内分批喂完。
          - 按字符预算（screen_batch_chars）自适应切批，不再固定篇数/批。
          - 批次相互独立，支持并发（每批独立 AIClient，无共享状态）。

        Args:
            patent_doc: 本申请 PatentDocument
            details_dir: 存放独立 JSON 的目录路径
            signals: WorkerSignals（用于日志和进度）
            log_dir: AI 交互日志目录（可选，每批写子目录避免并发冲突）
            concurrency: 批并发数，None 用 settings.analysis_screen_concurrency
            only_pubs: 只筛这些公布号（历史记录库已评分的跳过 AI）；None 全量

        Returns:
            按 relevance_score 降序排列的全部专利列表
        """
        from pathlib import Path

        detail_path = Path(details_dir)
        if not detail_path.exists():
            return []

        # 加载所有成功抓取的专利（有 claims 才参与广筛）
        patents = []
        for f in sorted(detail_path.glob("*.json")):
            try:
                d = json_module.loads(f.read_text(encoding="utf-8"))
                if d.get("fetch_status") == "ok" and d.get("claims"):
                    if only_pubs is not None:
                        pub = d.get("publication_number", "")
                        if pub not in only_pubs:
                            continue
                    patents.append(d)
            except Exception:
                pass

        if not patents:
            return []

        content_mode = self.settings.analysis_screen_content
        per_limit = self.settings.analysis_screen_claims_limit
        batch_chars = self.settings.analysis_screen_batch_chars
        if concurrency is None:
            concurrency = self.settings.analysis_screen_concurrency
        # 本申请侧：按 content_mode 生成本申请概要（模式感知）。
        #   embodiments 模式锚点=实施方式，本申请具体实施方式给足预算（per_limit）；
        #   claims+embodiments 各占一半预算；claims 模式不给实施方式。
        if content_mode == "embodiments":
            emb_budget = per_limit
        elif content_mode == "claims+embodiments":
            emb_budget = per_limit // 2
        else:
            emb_budget = 0
        patent_summary = self._build_patent_summary(
            patent_doc, mode=content_mode, emb_chars=emb_budget)
        total = len(patents)

        mode_label = {"claims": "权利要求", "embodiments": "具体实施方式",
                      "claims+embodiments": "权利要求+具体实施方式"}.get(
                          content_mode, content_mode)
        if signals:
            signals.log.emit("INFO",
                f"  Claims 广筛: 加载 {total} 篇, 内容={mode_label} "
                f"(≤{per_limit}字/篇), 按 {batch_chars} 字/批自适应分批 (并发 {concurrency})")

        def _get_emb(p: dict) -> str:
            """具体实施方式：优先用下载时存的字段，旧缓存兜底现抽，并挂回专利 dict"""
            if not p.get("embodiments"):
                p["embodiments"] = extract_embodiments(p.get("description") or "")
            return p["embodiments"]

        def _patent_content(p: dict) -> str:
            """按 content_mode 生成单篇对比文件的内容块"""
            claims = p.get("claims") or ""
            if content_mode == "embodiments":
                emb = _get_emb(p)[:per_limit]
                if not emb:
                    return (f"- 具体实施方式: (无，退回权利要求)\n"
                            f"- 权利要求:\n{claims[:per_limit]}\n")
                return f"- 具体实施方式:\n{emb}\n"
            if content_mode == "claims+embodiments":
                half = per_limit // 2
                emb = _get_emb(p)[:half]
                return (f"- 权利要求:\n{claims[:half]}\n"
                        f"- 具体实施方式:\n{emb or '(无)'}\n")
            # claims（默认）
            return f"- 权利要求:\n{claims[:per_limit]}\n"

        # ── 自适应分批：逐篇构建文本块，累计超预算开新批 ──
        batches = []
        current_batch = []
        current_chars = 0
        for i, p in enumerate(patents):
            pub = p.get("publication_number", "?")
            block = (
                f"### [{i+1}] {pub}\n"
                f"- 标题: {p.get('title', '')}\n"
                f"- IPC: {p.get('ipc', '')}\n"
                f"- 申请人: {p.get('applicant', '')}\n"
                + _patent_content(p)
            )
            block_len = len(block)
            if current_batch and current_chars + block_len > batch_chars:
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
            current_batch.append((p, block))
            current_chars += block_len
        if current_batch:
            batches.append(current_batch)

        num_batches = len(batches)
        if signals:
            sizes = ", ".join(str(len(b)) for b in batches)
            signals.log.emit("INFO", f"  共分 {num_batches} 批: 各批篇数 [{sizes}]")

        # 每批独立 client + 独立日志子目录，避免并发共享状态冲突
        def _run_batch(batch_idx: int):
            batch = batches[batch_idx]
            from src.ai_client import AIClient
            client = AIClient(self.settings, provider=self._provider)
            if log_dir:
                from pathlib import Path as _P
                client.set_log_dir(str(_P(log_dir) / f"batch_{batch_idx+1:02d}"))

            candidates_text = "\n".join(block for _, block in batch)

            user_prompt = render_template(
                load_prompt(self.settings, "screen_claims", "user",
                            SCREEN_CLAIMS_FALLBACK_USER_PROMPT),
                patent_summary=patent_summary,
                batch_number=batch_idx + 1, num_batches=num_batches,
                batch_count=len(batch), candidates_text=candidates_text)

            mode_file = _SCREEN_CLAIMS_MODE_FILE.get(content_mode, "claims")
            system_prompt = render_template(
                load_prompt(self.settings, "screen_claims",
                            f"system_{mode_file}",
                            SCREEN_CLAIMS_FALLBACK_SYSTEM_PROMPTS[mode_file]),
                total=len(batch))

            if signals:
                signals.log.emit("INFO",
                    f"  发送第{batch_idx+1}批 ({len(batch)}篇) 给 AI 评分...")

            try:
                max_tokens = max(16384, len(batch) * 256)
                response = client.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens, temperature=0.2,
                    model=self.settings.ai_screen_model)
                return self._parse_response(response, [p for p, _ in batch])
            except Exception as e:
                if signals:
                    signals.log.emit("ERROR",
                        f"  第{batch_idx+1}批评分失败: {e}")
                # 失败时保留原始数据，给默认分
                default = []
                for p, _ in batch:
                    p["fulltext_score"] = p.get("relevance_score", 30)
                    p["fulltext_reason"] = f"评分异常: {str(e)[:40]}"
                    p["key_features"] = []
                    default.append(p)
                return default

        all_scored = []
        if concurrency > 1 and num_batches > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = [ex.submit(_run_batch, idx)
                           for idx in range(num_batches)]
                for idx, fut in enumerate(futures):
                    try:
                        scored = fut.result()
                    except Exception as e:
                        scored = []
                        if signals:
                            signals.log.emit("ERROR",
                                f"  第{idx+1}批执行异常: {e}")
                    if scored:
                        all_scored.extend(scored)
                    if signals:
                        signals.progress.emit(
                            45 + int((idx + 1) / num_batches * 20),
                            f"Claims 广筛 第{idx+1}/{num_batches}批")
        else:
            for idx in range(num_batches):
                scored = _run_batch(idx)
                if scored:
                    all_scored.extend(scored)
                if signals:
                    signals.progress.emit(
                        45 + int((idx + 1) / num_batches * 20),
                        f"Claims 广筛 第{idx+1}/{num_batches}批")

        # 按分数降序排列
        all_scored.sort(
            key=lambda x: x.get("fulltext_score", x.get("relevance_score", 0)),
            reverse=True)

        if signals:
            signals.log.emit("SUCCESS",
                f"  Claims 广筛完成: {len(all_scored)} 篇, 共 {num_batches} 批")

        return all_scored

    # ================================================================
    # 内部方法
    # ================================================================

    def _build_patent_summary(self, patent_doc, mode: str = "claims",
                              emb_chars: int = 0) -> str:
        """生成本申请概要（模式感知）。

        模式与对比文件内容侧（screen_claims_all 的 content_mode）对齐：
          "claims"             → 对比文件只发权要：锚点=全量权利要求，不给实施方式
          "embodiments"        → 对比文件只发实施方式：锚点=标题+摘要+具体实施方式
          "claims+embodiments" → 对比文件发权要+实施方式：全量权利要求+具体实施方式

        Args:
            patent_doc: 本申请 PatentDocument
            mode: 内容模式（见上），决定摘要由哪些部分构成
            emb_chars: 具体实施方式字符数，0 表示不含该部分
        """
        parts = []
        if patent_doc.title:
            parts.append(f"**发明名称**: {patent_doc.title}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC分类**: {', '.join(patent_doc.ipc_classifications)}")
        abstract = patent_doc.abstract
        if abstract:
            parts.append(f"**摘要**: {abstract}")

        # 权利要求部分：embodiments 模式不给权利要求（锚点是具体实施方式，
        # 标题+摘要已提供背景），其余模式全量
        if patent_doc.claims and mode != "embodiments":
            parts.append(f"**权利要求** ({len(patent_doc.claims)}项):\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(patent_doc.claims)))

        # 具体实施方式部分：仅 embodiments / claims+embodiments 模式附带
        if emb_chars and patent_doc.description:
            emb = extract_embodiments(patent_doc.description, emb_chars)
            if emb:
                parts.append(f"**说明书具体实施方式**:\n{emb}")
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
                               "suggested_use": item.get("suggested_use", ""),
                               "key_features": item.get("key_features", [])})
        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return result
