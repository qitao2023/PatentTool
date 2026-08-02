"""
对比文件历史记录库 — 跨运行累积每个对比文件的评分/评述记录。

同一申请文件会多次检索（检索式迭代），每个对比文件需要留下记录：
  - 广筛评分（best_score / latest_score / reason / key_features）
  - 终选评述（detailed_review，评述过即可复用，避免重复调贵模型）

存储: {pdf所在目录}/对比历史_{申请公布号}.json
与现有 本申请_{pub}.json、patent_cache/ 同级，随申请文件走，跨会话复用。
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional


def history_path(base_dir: str, patent_number: str) -> Path:
    """历史记录库文件路径（按申请公布号命名，随 PDF 所在目录走）"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(patent_number or "unknown"))
    return Path(base_dir) / f"对比历史_{safe}.json"


def _new_record(pub: str) -> dict:
    return {
        "publication_number": pub,
        "title": "", "applicant": "", "ipc": "", "publication_date": "",
        "best_score": 0, "latest_score": 0,
        "best_reason": "", "key_features": [],
        "source_queries": [], "run_times": [],
        "detail_file": "", "detailed_review": None,
    }


class ScreeningHistory:
    """对比文件历史记录库（按申请公布号一个 JSON 文件）"""

    def __init__(self, base_dir: str | Path, patent_number: str):
        """base_dir: 申请 PDF 所在目录（历史文件随申请文件走）"""
        self.base_dir = Path(base_dir)
        self.path = history_path(self.base_dir, patent_number)
        self.patent_number = patent_number
        self._records: dict[str, dict] = {}
        self._load()

    @classmethod
    def from_pdf(cls, pdf_path: str | Path, patent_number: str) -> "ScreeningHistory":
        """从申请 PDF 路径创建（目录 = PDF 所在目录）"""
        return cls(Path(pdf_path).parent, patent_number)

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._records = data.get("records", {})
            except Exception:
                self._records = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "patent_number": self.patent_number,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "count": len(self._records),
                "records": self._records,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8")

    # ── 查询 ──────────────────────────────────────────────────────
    def has_record(self, pub: str) -> bool:
        return pub in self._records

    def get(self, pub: str) -> Optional[dict]:
        return self._records.get(pub)

    def all(self) -> list[dict]:
        """全部记录，按 best_score 降序"""
        recs = list(self._records.values())
        recs.sort(key=lambda r: r.get("best_score", 0), reverse=True)
        return recs

    # ── 广筛结果合并 ──────────────────────────────────────────────
    def merge_screened(self, patents: list[dict], source_query: str = ""):
        """广筛结果 upsert。best_score 取历史最高，追加 run_times/source_queries。

        Args:
            patents: 含 publication_number / title / applicant / ipc /
                     publication_date / (fulltext_score|relevance_score) /
                     (fulltext_reason|relevance_reason) / key_features 的列表。
                    若携带 _detail_file 键则一并存入 detail_file。
        """
        now = datetime.now().isoformat(timespec="seconds")
        for p in patents:
            pub = (p.get("publication_number") or "").strip()
            if not pub:
                continue
            rec = self._records.setdefault(pub, _new_record(pub))

            # 元数据：仅在缺失时补
            for field in ("title", "applicant", "ipc", "publication_date"):
                if not rec.get(field) and p.get(field):
                    rec[field] = p.get(field)

            score = (p.get("fulltext_score") or p.get("relevance_score") or 0)
            rec["latest_score"] = score
            if score > rec["best_score"]:
                rec["best_score"] = score
                reason = (p.get("fulltext_reason")
                          or p.get("relevance_reason") or "")
                if reason:
                    rec["best_reason"] = reason
                kf = p.get("key_features") or []
                if kf:
                    rec["key_features"] = list(kf)

            if source_query and source_query not in rec["source_queries"]:
                rec["source_queries"].append(source_query)
                # 有界：防止单个对比文件累积过多检索式
                if len(rec["source_queries"]) > 50:
                    rec["source_queries"] = rec["source_queries"][-50:]

            rec["run_times"].append(now)
            if len(rec["run_times"]) > 30:
                rec["run_times"] = rec["run_times"][-30:]

            if p.get("_detail_file") and not rec["detail_file"]:
                rec["detail_file"] = p["_detail_file"]

    # ── 终选评述 ──────────────────────────────────────────────────
    def has_detailed_review(self, pub: str) -> bool:
        rec = self._records.get(pub)
        return bool(rec and rec.get("detailed_review"))

    def set_detailed_review(self, pub: str, review: dict):
        rec = self._records.setdefault(pub, _new_record(pub))
        rec["detailed_review"] = {
            **review,
            "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        }

    # ── 终选候选池 ────────────────────────────────────────────────
    def best_pool(self, top_n: int = 50, min_score: int = 55) -> list[dict]:
        """终选候选池：best_score 达标且非空，取前 top_n（已按分降序）。

        只收"比较好"的对比文件，低分记录永远不进候选池（不浪费贵模型）。
        """
        pool = [r for r in self.all()
                if r.get("best_score", 0) >= min_score]
        return pool[:top_n]
