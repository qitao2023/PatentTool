"""
多检索式批量测试 — CLI 版本。

用法:
  python -m src.test_multi_query <queries.json>
  python -m src.test_multi_query <queries.json> --output-dir D:/my_test
  python -m src.test_multi_query <queries.json> --max-results 100 --concurrency 3

输入 JSON 格式:
{
  "test_name": "测试名称",
  "max_results": 100,
  "queries": [
    "EN_AB:(IGZO AND back gate)",
    "IGZO AND (背栅 OR 底栅)"
  ]
}
"""
import asyncio
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings


class ConsoleSignals:
    """轻量控制台信号，模拟 WorkerSignals 接口"""

    class log:
        @staticmethod
        def emit(level: str, msg: str):
            prefix = {"INFO": "  ", "SUCCESS": "✅", "WARN": "⚠️",
                       "ERROR": "❌", "DEBUG": "🔍"}.get(level, "  ")
            print(f"  {prefix} {msg}")

    class progress:
        @staticmethod
        def emit(pct: int, msg: str):
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {pct:3d}% {msg}", end="", flush=True)
            if pct >= 100:
                print()


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")


async def run_multi_query(queries: list[str], settings: Settings,
                          test_name: str = "",
                          max_results: int = 100,
                          concurrency: int = 1,
                          output_dir: str | None = None):
    """执行多检索式批量测试管线。"""

    signals = ConsoleSignals()

    # ── 输出目录 ──
    if output_dir:
        out = Path(output_dir)
    else:
        name = re.sub(r'[\\/:*?"<>|]', '_', test_name or "batch_test")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path.cwd() / "data" / "output" / "test_multi" / f"{name}_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    detail_dir = out / "02_patent_details"
    detail_dir.mkdir(parents=True, exist_ok=True)

    _save_json(out / "queries.json", {
        "test_name": test_name,
        "max_results": max_results,
        "concurrency": concurrency,
        "queries": queries,
    })

    # ================================================================
    # 阶段1: 逐条搜索摘要
    # ================================================================
    print("=" * 60)
    print(f"  批量检索测试: {len(queries)} 检索式 × {max_results} 条/式")
    print("=" * 60)

    from src.web_automation.browser_manager import BrowserManager
    from src.web_automation.human_behavior import HumanBehavior
    from src.web_automation.patentscope_scraper import (
        PatentscopeScraper, _safe_filename)

    print("\n[阶段1] 搜索摘要")
    browser_mgr = BrowserManager(settings)
    context, page = await browser_mgr.launch_with_retry(max_retries=2)
    print("  ✅ 浏览器就绪")

    human = HumanBehavior(settings)
    scraper = PatentscopeScraper(page, settings, human)

    all_abstracts = []
    per_query_stats = []
    per_query_dir = out / "per_query"
    per_query_dir.mkdir(parents=True, exist_ok=True)

    MAX_SEARCH_RETRIES = 3

    if settings.search_source == "google" and queries:
        # ══ Google：并行搜索全部检索式（多标签页）══
        from src.web_automation.google_patents import (
            search_abstracts_parallel as gsearch_parallel)
        q_strings = [str(q).strip() for q in queries]
        print(f"  [阶段1] 并行搜索 {len(queries)} 个检索式 "
              f"(并发 {settings.search_search_concurrency})")
        per_query = await gsearch_parallel(
            page, q_strings, max_results=max_results,
            signals=signals, concurrency=settings.search_search_concurrency)

        # ── 失败的检索式：切浏览器 + 冷却 + 重试（最多3轮）──
        failed_idx = [i for i, r in enumerate(per_query) if r.get("error")]
        retry_round = 0
        while failed_idx and retry_round < MAX_SEARCH_RETRIES:
            retry_round += 1
            err_text = "\n".join(per_query[i]["error"] or "" for i in failed_idx)
            is_403 = "403" in err_text
            is_net_error = any(kw in err_text for kw in (
                "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                "NS_BINDING_ABORTED", "net::ERR_",
            ))
            if not (is_403 or is_net_error):
                break  # 非限流错误，重试无意义
            if is_403:
                BrowserManager.switch_channel(on_403=True)
                cool = 10 + retry_round * 5
                reason = "403"
            else:
                BrowserManager.switch_channel()
                cool = 3 + retry_round * 2
                reason = "网络中断"
            print(f"  ⚠️ 并行搜索 {len(failed_idx)} 个检索式遇 {reason}，"
                  f"冷却 {cool}s 重试 ({retry_round}/{MAX_SEARCH_RETRIES})...")
            await browser_mgr.close()
            await asyncio.sleep(cool)
            try:
                context, page = await browser_mgr.launch_with_retry(max_retries=1)
            except Exception:
                await asyncio.sleep(2)
                context, page = await browser_mgr.launch_with_retry(max_retries=1)
            human = HumanBehavior(settings)
            scraper = PatentscopeScraper(page, settings, human)
            retry_res = await gsearch_parallel(
                page, [q_strings[i] for i in failed_idx],
                max_results=max_results, signals=signals,
                concurrency=min(settings.search_search_concurrency, len(failed_idx)))
            for k, i in enumerate(failed_idx):
                per_query[i] = retry_res[k]
            failed_idx = [i for i in failed_idx if per_query[i].get("error")]

        # ── 收尾：逐式存盘 + 日志 ──
        for q_idx, q_str in enumerate(queries):
            q_str = q_str.strip()
            if not q_str:
                continue
            res = per_query[q_idx]
            abstracts = res.get("abstracts", [])
            error_msg = res.get("error")
            for a in abstracts:
                a["source_query"] = q_str
            all_abstracts.append(abstracts)
            _save_json(
                per_query_dir / f"{q_idx + 1:02d}_abstracts.json",
                {"query": q_str, "count": len(abstracts),
                 "error": error_msg, "results": abstracts})
            per_query_stats.append({
                "index": q_idx + 1, "query": q_str,
                "results_count": len(abstracts), "error": error_msg,
            })
            status = "✅" if not error_msg else "❌"
            print(f"  {status} 检索式{q_idx + 1}: {len(abstracts)} 篇摘要")
    else:
        # ══ WIPO / 单检索式：串行（原逻辑）══
        for q_idx, q_str in enumerate(queries):
            q_str = q_str.strip()
            if not q_str:
                continue

            label = f"检索式{q_idx + 1}"
            print(f"\n  --- {label} / {len(queries)} ---")
            print(f"  {q_str}")

            abstracts = []
            error_msg = None
            for attempt in range(1, MAX_SEARCH_RETRIES + 1):
                try:
                    abstracts = await scraper.search_abstracts(
                        q_str, max_results=max_results, signals=signals)
                    break
                except Exception as e:
                    err_msg = str(e)
                    is_403 = "403" in err_msg
                    is_net_error = any(kw in err_msg for kw in (
                        "NS_ERROR_NET_INTERRUPT", "NS_ERROR_NET_RESET",
                        "NS_ERROR_NET_TIMEOUT", "NS_ERROR_CONNECTION_REFUSED",
                        "NS_BINDING_ABORTED", "net::ERR_",
                    ))
                    if (is_403 or is_net_error) and attempt < MAX_SEARCH_RETRIES:
                        if is_403:
                            BrowserManager.switch_channel(on_403=True)
                            cool = 10 + attempt * 5
                        else:
                            BrowserManager.switch_channel()
                            cool = 3 + attempt * 2
                        print(f"  ⚠️ 遇到{'403' if is_403 else '网络中断'}，"
                              f"冷却 {cool}s 重试 ({attempt}/{MAX_SEARCH_RETRIES})...")
                        await browser_mgr.close()
                        await asyncio.sleep(cool)
                        try:
                            context, page = await browser_mgr.launch_with_retry(max_retries=1)
                        except Exception:
                            await asyncio.sleep(2)
                            context, page = await browser_mgr.launch_with_retry(max_retries=1)
                        human = HumanBehavior(settings)
                        scraper = PatentscopeScraper(page, settings, human)
                    else:
                        error_msg = str(e)
                        break

            for a in abstracts:
                a["source_query"] = q_str
            all_abstracts.append(abstracts)

            _save_json(
                per_query_dir / f"{q_idx + 1:02d}_abstracts.json",
                {"query": q_str, "count": len(abstracts),
                 "error": error_msg, "results": abstracts})

            per_query_stats.append({
                "index": q_idx + 1, "query": q_str,
                "results_count": len(abstracts), "error": error_msg,
            })

            status = "✅" if not error_msg else "❌"
            print(f"  {status} {label}: {len(abstracts)} 篇摘要")

            if q_idx < len(queries) - 1:
                await human.inter_search_delay(q_idx + 1)

    await browser_mgr.close()

    # ================================================================
    # 轮询合并去重
    # ================================================================
    print(f"\n{'='*60}")
    print("  去重合并...")
    seen = set()
    unique_abstracts = []
    max_batch = max((len(b) for b in all_abstracts), default=0)
    for i in range(max_batch):
        for batch in all_abstracts:
            if i < len(batch):
                a = batch[i]
                key = a.get("publication_number") or a.get("doc_id", "")
                if key and key not in seen:
                    seen.add(key)
                    unique_abstracts.append(a)

    total_before = sum(len(b) for b in all_abstracts)
    total_unique = len(unique_abstracts)
    print(f"  ✅ 去重: {total_before} 篇 → {total_unique} 篇唯一专利")

    _save_json(out / "01_all_abstracts.json", {
        "stage": "merged_abstracts",
        "timestamp": datetime.now().isoformat(),
        "total_before_dedup": total_before,
        "total_unique": total_unique,
        "per_query": per_query_stats,
        "results": unique_abstracts,
    })

    if total_unique == 0:
        print("\n  ⚠️ 所有检索式均无结果，停止")
        return

    # ================================================================
    # 阶段2: 并行下载完整详情
    # ================================================================
    print(f"\n{'='*60}")
    print(f"  [阶段2] 下载 {total_unique} 篇完整详情 (并发 {concurrency})...")

    browser_mgr2 = BrowserManager(settings)
    context2, page2 = await browser_mgr2.launch_with_retry(max_retries=2)
    human2 = HumanBehavior(settings)
    scraper2 = PatentscopeScraper(page2, settings, human2)

    await scraper2.fetch_details_parallel(
        unique_abstracts, str(detail_dir),
        concurrency=concurrency, signals=signals)

    await browser_mgr2.close()

    # ================================================================
    # 报告
    # ================================================================
    print(f"\n{'='*60}")
    print("  数据质量分析...")

    from src.web_automation.patentscope_scraper import is_cached_patent_valid

    detail_files = sorted(detail_dir.glob("*.json"))
    succeeded = 0
    cached = 0
    failed = 0
    failed_list = []
    quality = {"with_claims": 0, "with_description": 0,
               "with_abstract": 0, "with_ipc": 0,
               "total_claims_chars": 0, "total_desc_chars": 0}
    succeeded_patents = []
    # 按下载源统计（体现 download_source 参数效果）
    source_stats = {"google_patents": 0, "patentscope": 0, "unknown": 0}
    SOURCE_LABEL = {"google_patents": "Google Patents",
                    "patentscope": "PATENTSCOPE", "unknown": "未知"}

    for fpath in detail_files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            failed += 1
            failed_list.append({"file": fpath.name, "error": "JSON解析失败"})
            continue

        status = data.get("fetch_status", "")
        src = data.get("_source", "") or "patentscope"
        if src not in source_stats:
            src = "unknown"
        if status == "ok":
            if is_cached_patent_valid(data):
                succeeded += 1
                source_stats[src] += 1
                pub = data.get("publication_number", fpath.stem)
                if data.get("claims"):
                    quality["with_claims"] += 1
                    quality["total_claims_chars"] += len(data["claims"])
                if data.get("description"):
                    quality["with_description"] += 1
                    quality["total_desc_chars"] += len(data["description"])
                if data.get("abstract"):
                    quality["with_abstract"] += 1
                if data.get("ipc"):
                    quality["with_ipc"] += 1
                succeeded_patents.append({
                    "doc_id": data.get("doc_id", ""),
                    "publication_number": pub,
                    "source": src,
                    "title": str(data.get("title", ""))[:80],
                })
            else:
                failed += 1
                failed_list.append({
                    "file": fpath.name,
                    "doc_id": data.get("doc_id", ""),
                    "error": "缓存内容无效",
                })
        elif status == "failed":
            failed += 1
            failed_list.append({
                "file": fpath.name,
                "doc_id": data.get("doc_id", ""),
                "error": data.get("error", "未知错误"),
            })
        else:
            if is_cached_patent_valid(data):
                cached += 1
            else:
                failed += 1
                failed_list.append({"file": fpath.name, "error": "缓存无效"})

    avg_claims = quality["total_claims_chars"] // max(succeeded, 1)
    avg_desc = quality["total_desc_chars"] // max(succeeded, 1)
    src_name = "google" if settings.search_source == "google" else "wipo"

    print(f"  ✅ 下载成功: {succeeded}  缓存: {cached}  失败: {failed}")
    print(f"  检索引擎: {src_name} | 实际来源: "
          f"Google={source_stats['google_patents']}  "
          f"PATENTSCOPE={source_stats['patentscope']}")
    print(f"  数据质量: 有权利要求 {quality['with_claims']}/{succeeded}"
          f"  | 有说明书 {quality['with_description']}/{succeeded}")
    print(f"  平均权利要求: {avg_claims} 字 | 平均说明书: {avg_desc} 字")

    # JSON 报告
    report = {
        "timestamp": datetime.now().isoformat(),
        "search_source": src_name,
        "per_query": per_query_stats,
        "total_before_dedup": total_before,
        "total_unique": total_unique,
        "download_stats": {"succeeded": succeeded, "cached": cached,
                           "failed": failed},
        "source_stats": source_stats,
        "succeeded_patents": succeeded_patents,
        "data_quality": {
            "with_claims": quality["with_claims"],
            "with_description": quality["with_description"],
            "with_abstract": quality["with_abstract"],
            "with_ipc": quality["with_ipc"],
            "average_claims_length": avg_claims,
            "average_description_length": avg_desc,
        },
        "failed_patents": failed_list,
    }
    _save_json(out / "report.json", report)

    # 文本报告
    lines = ["=" * 70, "  批量检索式测试报告", "=" * 70]
    lines.append(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  检索式数: {len(per_query_stats)}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("  各检索式结果")
    lines.append("-" * 70)
    for s in per_query_stats:
        st = "✓" if not s.get("error") else "✗"
        lines.append(
            f"  [{s['index']:2d}] {st} {s.get('results_count', 0):4d} 篇  "
            f"{s.get('query', '')[:70]}")
        if s.get("error"):
            lines.append(f"       错误: {s['error'][:120]}")
    lines.append("")
    lines.append("-" * 70)
    lines.append("  总体统计")
    lines.append("-" * 70)
    lines.append(f"  各检索式合计: {total_before} 篇")
    lines.append(f"  去重后唯一:   {total_unique} 篇")
    if total_before > 0:
        lines.append(f"  去重率:       "
                     f"{((1 - total_unique / total_before) * 100):.1f}%")
    lines.append(f"  下载成功:     {succeeded} 篇")
    lines.append(f"  缓存命中:     {cached} 篇")
    lines.append(f"  下载失败:     {failed} 篇")
    lines.append(f"  检索引擎:     {src_name}")
    lines.append(f"  实际来源:     Google={source_stats['google_patents']}  "
                 f"PATENTSCOPE={source_stats['patentscope']}")
    if total_unique > 0:
        lines.append(f"  成功率:       "
                     f"{(succeeded + cached) / total_unique * 100:.1f}%")
    if succeeded > 0:
        lines.append("")
        lines.append("-" * 70)
        lines.append("  数据质量")
        lines.append("-" * 70)
        lines.append(f"  有权利要求:   {quality['with_claims']}/{succeeded}")
        lines.append(f"  有说明书:     {quality['with_description']}/{succeeded}")
        lines.append(f"  有摘要:       {quality['with_abstract']}/{succeeded}")
        lines.append(f"  有IPC分类:    {quality['with_ipc']}/{succeeded}")
    if succeeded_patents:
        lines.append("")
        lines.append("-" * 70)
        lines.append("  各文件下载来源")
        lines.append("-" * 70)
        for sp in succeeded_patents:
            lines.append(
                f"  [{SOURCE_LABEL.get(sp.get('source','unknown'),'?')}] "
                f"{sp.get('publication_number','?')}  {sp.get('title','')[:50]}")
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  输出目录: {out}")
    lines.append("=" * 70)

    (out / "report.txt").write_text("\n".join(lines), encoding="utf-8")

    # 输出摘要
    print(f"\n{'='*60}")
    print("  ✅ 批量测试完成!")
    print(f"  输出目录: {out}")
    print(f"  {out / 'report.json'}")
    print(f"  {out / 'report.txt'}")


def main():
    if len(sys.argv) < 2:
        print("用法: python -m src.test_multi_query <queries.json> [选项]")
        print()
        print("选项:")
        print("  --output-dir DIR    输出目录（默认 data/output/test_multi/）")
        print("  --max-results N     每检索式结果上限（默认 200）")
        print("  --concurrency N     下载并发数（默认 1）")
        print()
        print("JSON 格式:")
        print('  {"test_name": "名称", "queries": ["检索式1", ...]}')
        print('  或 {"queries": ["检索式1", "检索式2", ...]}')
        sys.exit(1)

    queries_file = Path(sys.argv[1])
    if not queries_file.exists():
        print(f"❌ 文件不存在: {queries_file}")
        sys.exit(1)

    # 解析参数
    output_dir = None
    max_results = 100
    concurrency = 1
    engine = None
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--output-dir" and i + 1 < len(sys.argv):
            output_dir = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--max-results" and i + 1 < len(sys.argv):
            max_results = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--concurrency" and i + 1 < len(sys.argv):
            concurrency = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--engine" and i + 1 < len(sys.argv):
            engine = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    settings = Settings()
    if engine:
        if engine not in ("wipo", "google"):
            print(f"❌ 未知引擎: {engine}（可选 wipo|google）")
            sys.exit(1)
        settings._raw.setdefault("search", {})["search_source"] = engine

    # 加载查询
    data = json.loads(queries_file.read_text(encoding="utf-8"))
    test_name = data.get("test_name", queries_file.stem)
    queries = data.get("queries", [])

    # 兼容旧格式：queries 里可能是 dict 列表
    if queries and isinstance(queries[0], dict):
        queries = [q.get("query_string", q.get("query", str(q))) for q in queries]

    if "max_results" in data:
        max_results = data["max_results"]
    if "concurrency" in data:
        concurrency = data["concurrency"]

    if not queries:
        print("❌ queries 为空")
        sys.exit(1)

    print(f"加载: {len(queries)} 个检索式")
    print(f"测试名称: {test_name}")
    print(f"每式上限: {max_results}")
    print(f"并发: {concurrency}")
    print(f"引擎: {engine or settings.search_source}")
    print()

    asyncio.run(run_multi_query(
        queries, settings,
        test_name=test_name,
        max_results=max_results,
        concurrency=concurrency,
        output_dir=output_dir,
    ))


if __name__ == "__main__":
    main()
