"""
对比文件历史记录库 — 跨运行累积每个对比文件的评分/评述记录。

同一申请文件会多次检索（检索式迭代），每个对比文件需要留下记录：
  - 广筛评分（best_score / latest_score / reason / key_features）
  - 终选评述（detailed_review，评述过即可复用，避免重复调贵模型）

存储: {pdf所在目录}/对比历史_{申请公布号}.json
与现有 本申请_{pub}.json、patent_detail/ 同级，随申请文件走，跨会话复用。
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


# ================================================================
# 评分快照 — 每次「开始分析」运行导出一份带时间戳的评分文件，
# 综述（终选评述）时读全部快照合并，取历史最高分降序 top_n。
# 与 ScreeningHistory（累积去重）并存：快照保留每次运行的完整记录，
# 综述不再依赖单一累积文件。
# ================================================================


def score_snapshot_path(base_dir: str, patent_number: str,
                        timestamp: str) -> Path:
    """评分快照文件路径：每次运行一份（带时间戳），随 PDF 目录走。"""
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(patent_number or "unknown"))
    # 时间戳含冒号（如 2026-08-02T20:30:00），需清洗为 Windows 合法文件名
    safe_ts = re.sub(r'[\\/:*?"<>|\s]+', "_", str(timestamp or "unknown"))
    return Path(base_dir) / f"广筛评分_{safe}_{safe_ts}.json"


def save_score_snapshot(base_dir: str, patent_number: str, timestamp: str,
                        results: list[dict], source_queries=None,
                        content_mode: str = "") -> Path:
    """把当次 Claims 广筛结果存为评分快照文件。

    仅存分数 + 元数据 + detail_file 指针（不含全文，全文在固定 patent_detail/）。
    文件名带时间戳，多次运行互不覆盖。
    """
    path = score_snapshot_path(base_dir, patent_number, timestamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "patent_number": patent_number,
        "created_at": timestamp,
        "source_queries": source_queries or [],
        "content_mode": content_mode,
        "count": len(results),
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def list_score_snapshots(base_dir: str, patent_number: str) -> list[dict]:
    """列出该申请的所有评分快照（各在各自操作文件夹里），供综评前勾选。

    每次「开始分析」的评分文件存于该次运行的输出目录（自己的文件夹）：
      {base_dir}/{运行目录}/广筛评分_{safe}_{时间戳}.json
    这里扫描 base_dir 下所有子目录，按公布号前缀匹配。

    每项含 path / name / run_dir / created_at / count。
    """
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(patent_number or "unknown"))
    out = []
    for sub in sorted(Path(base_dir).glob("*/")):
        for fp in sorted(sub.glob(f"广筛评分_{safe}_*.json")):
            meta = {"path": str(fp), "name": fp.name,
                    "run_dir": sub.name, "created_at": "", "count": 0}
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                meta["created_at"] = data.get("created_at", "")
                meta["count"] = data.get("count", 0)
            except Exception:
                pass
            out.append(meta)
    return out


def load_score_snapshots(base_dir: str, patent_number: str,
                         min_score: int = 55, top_n: int = 50,
                         files: list | None = None) -> list[dict]:
    """综述用：读评分快照，合并取历史最高分，降序取 top_n。

    每个快照文件对应一次「开始分析」的 Claims 广筛结果；
    同一对比文件多次出现时取历史最高分（best_score）。

    Args:
        base_dir: PDF 所在目录（快照随 PDF 走）
        patent_number: 申请公布号
        min_score: 低于此分不进候选池
        top_n: 候选池上限
        files: 只读这些快照文件路径；None 表示 glob 全部
    """
    if files is None:
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(patent_number or "unknown"))
        files = []
        for sub in sorted(Path(base_dir).glob("*/")):
            files.extend(sorted(sub.glob(f"广筛评分_{safe}_*.json")))
    merged: dict[str, dict] = {}
    for fp in files:
        fp = Path(fp)
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in data.get("results", []) or []:
            pub = (r.get("publication_number") or "").strip()
            if not pub:
                continue
            score = r.get("fulltext_score") or r.get("relevance_score") or 0
            cur = merged.get(pub)
            if cur is None or score > (cur.get("best_score") or 0):
                entry = dict(r)
                entry["best_score"] = score
                entry["best_reason"] = (r.get("fulltext_reason")
                                        or r.get("relevance_reason") or "")
                merged[pub] = entry
    pool = [e for e in merged.values()
            if (e.get("best_score") or 0) >= min_score]
    pool.sort(key=lambda e: e.get("best_score") or 0, reverse=True)
    return pool[:top_n]
