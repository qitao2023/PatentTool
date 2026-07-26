"""
PATENTSCOPE 全流程命令行测试（无头模式）
用法: python -m src.test_full_flow [PDF路径]
"""
import asyncio, sys, json, re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import Settings
from src.pdf_extractor.extractor import PatentPDFExtractor
from src.query_generator.generator import QueryGenerator
from src.web_automation.human_behavior import HumanBehavior
from src.analysis.screener import PatentScreener


PDF_PATH = r"E:\01-claudecode\00-patent\01-20260724\本申请.PDF"
MAX_QUERIES = 2
MAX_RESULTS = 5


async def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else PDF_PATH
    settings = Settings()
    print("=" * 60)
    print("  PATENTSCOPE 全流程测试（无头模式）")
    print(f"  专利文件: {pdf_path}")
    print(f"  检索式数: {MAX_QUERIES}, 结果数/检索式: {MAX_RESULTS}")
    print("=" * 60)

    # ---- Step 0: 解析 PDF ----
    print("\n[0] 解析PDF...")
    extractor = PatentPDFExtractor(pdf_path)
    patent = extractor.extract()
    print(f"  标题: {patent.title}")
    print(f"  IPC: {', '.join(patent.ipc_classifications)}")
    print(f"  摘要: {patent.abstract[:100]}...")
    print(f"  权利要求: {len(patent.claims)} 项")

    # ---- Step 1: 生成检索式 ----
    print(f"\n[1] AI 生成 {MAX_QUERIES} 个 PATENTSCOPE 检索式...")
    gen = QueryGenerator(settings, provider="deepseek")
    queries = gen.generate(patent, max_queries=MAX_QUERIES)
    print(f"  生成 {len(queries)} 个检索式:")
    for i, q in enumerate(queries):
        print(f"    [{i+1}] {q.get('search_angle','')}")
        print(f"        {q.get('query_string','')}")

    # ---- Step 2: PATENTSCOPE 搜索摘要 ----
    print(f"\n[2] PATENTSCOPE 搜索摘要 ({len(queries)}检索式 × {MAX_RESULTS}条)...")
    from playwright.async_api import async_playwright

    all_abstracts = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, channel="msedge",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en", timezone_id="Asia/Shanghai")
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        human = HumanBehavior(settings)
        from src.web_automation.patentscope_scraper import PatentscopeScraper
        scraper = PatentscopeScraper(page, settings, human)

        for idx, q in enumerate(queries):
            q_str = q.get("query_string", "")
            print(f"  检索式 {idx+1}/{len(queries)}: {q_str}")
            results = await scraper.search_abstracts(q_str, max_results=MAX_RESULTS)
            for r in results:
                r["source_query"] = q_str
            all_abstracts.append(results)
            print(f"    获取 {len(results)} 条")

        await browser.close()

    # 去重
    seen = set()
    unique = []
    for batch in all_abstracts:
        for r in batch:
            key = r.get("doc_id") or r.get("publication_number", "")
            if key and key not in seen:
                seen.add(key)
                unique.append(r)

    print(f"\n  去重后: {len(unique)} 篇摘要")
    for i, r in enumerate(unique[:5]):
        print(f"    [{i+1}] {r.get('publication_number','?')} - {r.get('title','?')[:70]}")

    # ---- Step 3: AI 粗筛 ----
    print(f"\n[3] AI 从 {len(unique)} 篇中筛选最相关...")
    screener = PatentScreener(settings)
    screened = screener.screen(patent, unique, top_n=settings.analysis_top_n)
    print(f"  筛选出 {len(screened)} 篇:")
    for i, s in enumerate(screened):
        print(f"    [{i+1}] {s.get('publication_number','?')} "
              f"(相关度:{s.get('relevance_score','?')}) "
              f"{s.get('relevance_reason','')[:60]}")

    # ---- Step 4: 抓取全文 ----
    print(f"\n[4] 获取 {len(screened)} 篇全文...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, channel="msedge",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en", timezone_id="Asia/Shanghai")
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        human = HumanBehavior(settings)
        from src.web_automation.patentscope_scraper import PatentscopeScraper
        scraper = PatentscopeScraper(page, settings, human)

        enriched = await scraper.fetch_details_batch(screened)
        await browser.close()

    full_count = sum(1 for r in enriched if not r.get("_no_detail"))
    print(f"  全文获取: {full_count}/{len(enriched)}")

    # ---- Step 5: AI 对比分析 ----
    print(f"\n[5] AI 对比分析...")
    from src.analysis.comparator import PatentComparator
    comparator = PatentComparator(settings)
    comparisons = comparator.compare_batch(patent, enriched)
    from src.analysis.report import AnalysisReport
    report = AnalysisReport(patent_doc=patent, comparisons=comparisons, dedup_results=enriched)
    report.generate()
    print(f"  报告生成完成 ({len(report.markdown_content)} 字)")

    # ---- Step 6: OA 通知书 ----
    print(f"\n[6] AI 撰写审查意见通知书...")
    from src.analysis.oa_writer import OAWriter
    writer = OAWriter(settings)
    oa_md = writer.write(patent, comparisons, enriched)
    print(f"  通知书完成 ({len(oa_md)} 字)")

    # ---- 保存 ----
    patent_name = re.sub(r'[\\/:*?"<>|]', '_',
        (patent.publication_number or patent.title or "unknown"))[:80]
    run_dir = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).parent.parent / "data" / "output" / patent_name / run_dir
    out.mkdir(parents=True, exist_ok=True)

    def save(name, data):
        path = out / name
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(data, (dict, list)):
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            else:
                f.write(str(data))
        return path

    save("01_search_abstracts.json", {
        "stage": "search_abstracts", "total": len(unique),
        "queries": [q.get("query_string","") for q in queries], "results": unique})
    save("02_ai_screened.json", {
        "stage": "ai_screened", "total_before": len(unique),
        "total_after": len(screened), "results": screened})
    save("03_full_details.json", {
        "stage": "full_details", "total": len(enriched),
        "full_text_count": full_count, "results": enriched})
    save("04_analysis_report.md", report.markdown_content)
    save("05_审查意见通知书.md", oa_md)

    print(f"\n{'='*60}")
    print(f"  ✅ 全流程完成!")
    print(f"  输出目录: {out}")
    print(f"{'='*60}")

    # 打印通知书前 2000 字预览
    print(f"\n--- 审查意见通知书预览 ---")
    print(oa_md[:2000])


if __name__ == "__main__":
    asyncio.run(main())
