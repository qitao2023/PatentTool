"""
诊断：验证「全量 Claims 广筛」分批 / 解析 / 合并 / 历史记录库。

用法：
    python tools/debug_claims_screen.py [details_dir]

    details_dir 缺省时自动找 data/output/test_multi/ 下最新的 02_patent_details。
    用真实 API Key（config/.env 已配置）实际跑广筛，打印分批结构 + Top 结果。

可选环境变量：
    DEBUG_BATCH_CHARS=30000   强制小批次，制造多批验证分批逻辑
    DEBUG_CONCURRENCY=3       批并发
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.pdf_extractor.extractor import PatentDocument
from src.analysis.screener import PatentScreener
from src.analysis.history import ScreeningHistory


class _OverrideSettings(Settings):
    """覆盖广筛批次参数，便于制造多批验证"""

    def __init__(self, batch_chars: int | None, concurrency: int | None):
        super().__init__()
        self._batch_chars = batch_chars
        self._conc = concurrency

    @property
    def analysis_screen_batch_chars(self):
        return self._batch_chars or super().analysis_screen_batch_chars

    @property
    def analysis_screen_concurrency(self):
        return self._conc or super().analysis_screen_concurrency


def _find_details_dir(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            sys.exit(f"目录不存在: {p}")
        return p
    root = Path.cwd() / "data" / "output" / "test_multi"
    if not root.exists():
        sys.exit(f"未找到默认测试目录: {root}，请显式传入 details_dir")
    dirs = sorted(
        (d for d in root.glob("*") if (d / "02_patent_details").exists()),
        key=lambda d: d.stat().st_mtime, reverse=True)
    if not dirs:
        sys.exit("test_multi 下没有含 02_patent_details 的运行目录")
    return dirs[0] / "02_patent_details"


def main():
    import os
    import json as json_module

    details_dir = _find_details_dir(
        sys.argv[1] if len(sys.argv) > 1 else None)
    batch_chars = int(os.getenv("DEBUG_BATCH_CHARS", "0") or 0) or None
    concurrency = int(os.getenv("DEBUG_CONCURRENCY", "2"))

    settings = _OverrideSettings(batch_chars, concurrency)

    # 扫描现有数据规模
    files = sorted(details_dir.glob("*.json"))
    ok = 0
    with_claims = 0
    claims_chars = []
    for f in files:
        try:
            d = json_module.loads(f.read_text(encoding="utf-8"))
            if d.get("fetch_status") == "ok":
                ok += 1
            if d.get("claims"):
                with_claims += 1
                claims_chars.append(len(d["claims"]))
        except Exception:
            pass
    print(f"目录: {details_dir}")
    print(f"文件数: {len(files)} | fetch_ok: {ok} | 有claims: {with_claims}")
    if claims_chars:
        avg = sum(claims_chars) // len(claims_chars)
        print(f"claims 长度: 平均 {avg} 字, 最大 {max(claims_chars)}, "
              f"最小 {min(claims_chars)}")

    # 构造一个测试本申请（结构验证用，内容近似真实场景）
    patent_doc = PatentDocument(
        title="测试本申请（高k栅介质组成梯度）",
        abstract="本申请公开一种集成电路结构，栅介质层具有组成梯度，用于改善界面缺陷并提升器件可靠性。",
        claims=[
            "1. 一种集成电路结构，其特征在于包括栅介质层，所述栅介质层具有第一组成和第二组成，形成组成梯度。",
            "2. 根据权利要求1所述的结构，其中第一组成位于栅介质层靠近栅电极一侧。",
        ],
        ipc_classifications=["H01L29/51"],
    )

    print(f"\n{'='*60}\n启动 Claims 广筛 (batch_chars={batch_chars or settings.analysis_screen_batch_chars}, "
          f"concurrency={concurrency})\n{'='*60}")

    class _Signals:
        def __init__(self):
            self.progress = _Sink()
            self.log = _Sink()

    class _Sink:
        def emit(self, *a, **k):
            print("  [log]", a)

    screener = PatentScreener(settings)
    scored = screener.screen_claims_all(
        patent_doc, str(details_dir),
        signals=_Signals(),
        log_dir=str(Path(details_dir).parent / "ai_logs_debug"),
        concurrency=concurrency)

    print(f"\n{'='*60}\n广筛完成: {len(scored)} 篇")
    print(f"Top 15:")
    for i, r in enumerate(scored[:15], 1):
        pub = r.get("publication_number", "?")
        score = r.get("fulltext_score", r.get("relevance_score", "?"))
        reason = str(r.get("fulltext_reason", r.get("relevance_reason", "")))[:80]
        kf = "、".join(r.get("key_features") or [])[:80]
        print(f"  [{i:2d}] {pub:18s} 分数={score:<4} | {reason}")
        if kf:
            print(f"       features: {kf}")

    # ── 验证历史记录库合并 ──
    print(f"\n{'='*60}\n验证历史记录库合并...")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        hist = ScreeningHistory(tmp, "CN_TEST0001A")
        hist.merge_screened(scored)
        hist.save()
        print(f"  历史记录数: {len(hist.all())}")
        pool = hist.best_pool(top_n=5, min_score=40)
        print(f"  best_pool(top5, min=40): {len(pool)} 篇")
        for r in pool[:5]:
            print(f"    {r['publication_number']} best={r['best_score']} "
                  f"latest={r['latest_score']} runs={len(r['run_times'])}")
        # 再合并一次，验证 best_score 取 max、run_times 累积
        hist.merge_screened(scored)
        top = hist.all()[0] if hist.all() else {}
        print(f"  二次合并后: 记录 {len(hist.all())} 条, "
              f"首条 best={top.get('best_score')} run_times={len(top.get('run_times', []))}")


if __name__ == "__main__":
    main()
