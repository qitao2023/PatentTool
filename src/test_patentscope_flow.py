"""
PATENTSCOPE 全流程连接测试脚本

测试内容:
  1. 浏览器启动（无需登录）
  2. 搜索页结构分析
  3. 执行搜索 + 检查结果
  4. 切换 200 条/页 + 取第1页摘要
  5. 翻到第2页 + 取第2页摘要
  6. 总结报告

用法:
  python -m src.test_patentscope_flow [检索式]
  python -m src.test_patentscope_flow "EN_AB:(lithium AND battery) AND IC:(H01M)"
"""
import asyncio
import sys
import time
from pathlib import Path

# Windows 终端 UTF-8 编码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                   errors="replace", line_buffering=True)

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


DEFAULT_QUERY = "lithium battery"


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY

    print("=" * 60)
    print("  PATENTSCOPE 全流程连接测试")
    print("=" * 60)
    print(f"  检索式: {query}")
    print()

    settings = Settings()

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        # ============ Step 1: 启动浏览器 ============
        print("=" * 60)
        print("  Step 1: 启动浏览器（Edge + 反检测）")
        print("=" * 60)

        browser = await p.chromium.launch(
            headless=False,
            channel="msedge",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            locale="en",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        print("  ✅ 浏览器启动成功（PATENTSCOPE 无需登录）")
        print()

        # ============ Step 2: 搜索页结构分析 ============
        print("=" * 60)
        print("  Step 2: 搜索页结构分析")
        print("=" * 60)

        search_url = settings.patentscope_search_url
        print(f"  导航到: {search_url}")
        await page.goto(search_url, timeout=60000, wait_until="load")
        # JSF 页面可能有初始化跳转，等足够长让页面稳定
        await asyncio.sleep(5)

        # 检测关键元素
        elements = await page.evaluate(
            """() => {
            var result = {searchInput: false, searchButton: false, fieldSelect: false};
            var input = document.getElementById("simpleSearchForm:fpSearch:input");
            if (input && input.offsetParent !== null) result.searchInput = true;

            var buttons = document.querySelectorAll("button");
            buttons.forEach(function(b) {
                if (b.id && b.id.indexOf("fpSearch") >= 0 && b.offsetParent !== null) {
                    result.searchButton = true;
                }
            });

            var select = document.querySelector("select");
            if (select && select.offsetParent !== null) result.fieldSelect = true;

            result.title = document.title;
            result.bodyLen = (document.body.innerText || "").length;
            return result;
        }"""
        )

        print(f"  页面标题: {elements.get('title', 'N/A')}")
        print(f"  页面文本长度: {elements.get('bodyLen', 0)}")
        print(f"  搜索输入框: {'✅' if elements.get('searchInput') else '❌'}")
        print(f"  搜索按钮: {'✅' if elements.get('searchButton') else '❌'}")
        print(f"  字段选择器: {'✅' if elements.get('fieldSelect') else '❌'}")
        print()

        # ============ Step 3: 执行搜索 ============
        print("=" * 60)
        print("  Step 3: 执行检索")
        print("=" * 60)
        print(f"  检索式: {query}")

        await page.evaluate(
            f"""(query) => {{
            var input = document.getElementById("simpleSearchForm:fpSearch:input");
            if (!input) throw new Error("Search input not found");
            input.value = query;
            input.dispatchEvent(new Event("input", {{bubbles: true}}));
            input.dispatchEvent(new Event("change", {{bubbles: true}}));

            var buttons = document.querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++) {{
                if (buttons[i].id && buttons[i].id.indexOf("fpSearch") >= 0) {{
                    buttons[i].click();
                    return;
                }}
            }}
            throw new Error("Search button not found");
        }}""",
            query,
        )

        # 等待结果页
        try:
            await page.wait_for_url("**/result.jsf*", timeout=60000)
            print(f"  ✅ 搜索结果页: {page.url}")
        except Exception:
            print(f"  ⚠️ 未跳转到 result.jsf，当前 URL: {page.url}")

        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        print()

        # ============ Step 4: 检查结果表格 ============
        print("=" * 60)
        print("  Step 4: 检查搜索结果")
        print("=" * 60)

        result_count = await page.evaluate(
            "() => document.querySelectorAll("
            "'tr.trans-result-list-row, .ps-patent-result').length")
        print(f"  当前页条目: {result_count}")

        if result_count == 0:
            print("  ❌ 没有发现结果条目，测试终止")
            await context.close()
            await browser.close()
            return

        print()

        # ============ Step 5: 切到 200 并取第1页摘要 ============
        print("=" * 60)
        print("  Step 5: 切换 200 条/页，取第1页摘要")
        print("=" * 60)

        from src.web_automation.patentscope_scraper import PatentscopeScraper

        human = HumanBehavior(settings)
        scraper = PatentscopeScraper(page, settings, human)

        # 切换分页大小
        rows_before = await scraper._count_result_rows()
        print(f"  切换前行数: {rows_before}")

        await scraper._set_max_page_size()
        stable = await scraper._wait_for_results_stable(label="切换200")
        total = scraper._total_from_page_text
        print(f"  切换后稳定行数: {stable}  (总结果: {total or '?'})")

        # 取第1页摘要
        await scraper._wait_for_results()
        page1_items = await scraper._parse_results_table()
        print(f"  第1页摘要: {len(page1_items)} 篇")
        for i, item in enumerate(page1_items[:3]):
            pn = item.get('publication_number', '?')
            title = (item.get('title') or '?')[:60]
            print(f"    [{i+1}] {pn}  {title}")

        print()

        # ============ Step 6: 翻到第2页，取摘要 ============
        print("=" * 60)
        print("  Step 6: 翻到第2页，取摘要")
        print("=" * 60)

        has_next = await scraper._go_to_next_page()
        if not has_next:
            print("  ❌ 无法翻到第2页")
        else:
            print(f"  ✅ 已翻到第2页")
            await scraper._wait_for_results()
            await scraper._wait_for_results_stable(label="第2页")
            page2_items = await scraper._parse_results_table()
            print(f"  第2页摘要: {len(page2_items)} 篇")
            for i, item in enumerate(page2_items[:3]):
                pn = item.get('publication_number', '?')
                title = (item.get('title') or '?')[:60]
                print(f"    [{i+1}] {pn}  {title}")

        print()

        # ============ 总结 ============
        print("=" * 60)
        print("  ✅ PATENTSCOPE 翻页测试完成")
        print("=" * 60)
        print(f"  检索式: {query}")
        print(f"  总结果数: {total or '?'}")
        print(f"  分页大小: 200 条/页")
        print(f"  第1页: {len(page1_items)} 篇摘要")
        if has_next:
            print(f"  第2页: {len(page2_items)} 篇摘要")
        print(f"  翻页: {'✅ 正常' if has_next else '⚠️ 无第2页'}")

        print()
        print("  按 Enter 关闭浏览器...")
        input()

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
