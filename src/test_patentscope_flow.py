"""
PATENTSCOPE 全流程连接测试脚本

测试内容:
  1. 浏览器启动（无需登录）
  2. 搜索页结构分析
  3. 执行搜索 + 检查结果
  4. 详情页提取
  5. 返回结果列表
  6. 分页测试
  7. 总结报告

用法:
  python -m src.test_patentscope_flow [检索式]
  python -m src.test_patentscope_flow "EN_AB:(lithium AND battery) AND IC:(H01M)"
"""
import asyncio
import sys
import time
from pathlib import Path

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
        await page.goto(search_url, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=30000)
        await asyncio.sleep(2)

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

        await page.wait_for_load_state("networkidle", timeout=60000)
        await asyncio.sleep(3)
        print()

        # ============ Step 4: 检查结果表格 ============
        print("=" * 60)
        print("  Step 4: 检查结果列表")
        print("=" * 60)

        # 检查结果数
        count_text = await page.evaluate(
            """() => {
            var el = document.querySelector(".results-count");
            if (el) return el.textContent.trim();

            var body = document.body.innerText || "";
            var match = body.match(/(\\d[\\d,]*)\\s+results?/i);
            return match ? match[1] : "N/A";
        }"""
        )
        print(f"  搜索结果数: {count_text}")

        # 解析结果条目
        result_items = await page.evaluate(
            """() => {
            var rows = document.querySelectorAll("tr.trans-result-list-row, .ps-patent-result");
            var items = [];
            rows.forEach(function(row) {
                var numEl = row.querySelector(".ps-patent-result--title--patent-number");
                var titleEl = row.querySelector(".ps-patent-result--title--title");
                var linkEl = row.querySelector("a[href*='detail']");
                items.push({
                    number: numEl ? numEl.textContent.trim() : "?",
                    title: titleEl ? titleEl.textContent.trim().substring(0, 100) : "?",
                    detailUrl: linkEl ? linkEl.href : ""
                });
            });
            return items;
        }"""
        )

        print(f"  结果条目数: {len(result_items)}")
        for i, item in enumerate(result_items[:5]):
            print(f"    [{i+1}] {item['number']}")
            print(f"        {item['title'][:80]}")

        if len(result_items) == 0:
            print("  ❌ 没有发现结果条目，测试终止")
            await context.close()
            await browser.close()
            return

        print()

        # ============ Step 5: 详情页提取测试 ============
        print("=" * 60)
        print("  Step 5: 详情页提取测试（取第一条）")
        print("=" * 60)

        first_item = result_items[0]
        first_url = first_item.get("detailUrl", "")

        if not first_url:
            print("  ❌ 没有详情页链接")
        else:
            print(f"  详情URL: {first_url[:150]}")

            # 提取 docId
            import re

            doc_match = re.search(r"docId=([^&]+)", first_url)
            doc_id = doc_match.group(1) if doc_match else "UNKNOWN"
            print(f"  docId: {doc_id}")

            # 导航到详情页
            await page.goto(first_url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(2)

            # 提取数据
            detail = await page.evaluate(
                """() => {
                var result = {};
                var h1 = document.querySelector("h1");
                if (h1) result.title = h1.textContent.trim().substring(0, 150);

                var body = document.body.innerText || "";
                result.bodyLength = body.length;

                // 查找关键字段
                var rows = document.querySelectorAll("table tr, .ps-field");
                rows.forEach(function(row) {
                    var text = row.textContent.trim();
                    if (text.startsWith("IPC")) result.ipc = text.substring(0, 200);
                    if (text.startsWith("Applicant")) result.applicant = text.substring(0, 200);
                    if (text.startsWith("Inventor")) result.inventor = text.substring(0, 200);
                    if (text.startsWith("Publication Date")) result.pubDate = text.substring(0, 100);
                });

                // 摘要
                var absEl = document.querySelector("[class*='abstract'], [id*='abstract']");
                if (absEl) result.abstract = absEl.textContent.trim().substring(0, 300);

                return result;
            }"""
            )

            print(f"  标题: {detail.get('title', 'N/A')}")
            print(f"  IPC: {detail.get('ipc', 'N/A')[:100]}")
            print(f"  申请人: {detail.get('applicant', 'N/A')[:100]}")
            print(f"  发明人: {detail.get('inventor', 'N/A')[:100]}")
            print(f"  公开日: {detail.get('pubDate', 'N/A')[:100]}")
            print(f"  摘要: {detail.get('abstract', 'N/A')[:150]}")
            print(f"  正文长度: {detail.get('bodyLength', 0)}")

            # ============ Step 6: 返回结果列表 ============
            print()
            print("=" * 60)
            print("  Step 6: 返回结果列表")
            print("=" * 60)

            try:
                await page.go_back(timeout=15000)
                await page.wait_for_load_state("networkidle", timeout=30000)
                await asyncio.sleep(2)
                back_ok = "result.jsf" in page.url
                print(f"  {'✅' if back_ok else '❌'} 返回后URL: {page.url}")
            except Exception as e:
                print(f"  ❌ go_back 失败: {e}")

        print()

        # ============ Step 7: 分页测试 ============
        print("=" * 60)
        print("  Step 7: 分页测试")
        print("=" * 60)

        has_next = await page.evaluate(
            """() => {
            var nextBtn = document.querySelector(
                "a[id*='nextPage'], a[id*='navigationNext'], " +
                ".ui-paginator-next:not(.ui-state-disabled)"
            );
            return nextBtn !== null && nextBtn.offsetParent !== null;
        }"""
        )

        print(f"  有下一页: {'✅' if has_next else '❌ (可能只有一页或最后一页)'}")

        if has_next:
            try:
                next_btn = page.locator(
                    "a[id*='nextPage'], a[id*='navigationNext'], "
                    ".ui-paginator-next:not(.ui-state-disabled)"
                ).first
                await next_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                await asyncio.sleep(2)
                print(f"  ✅ 翻页成功: {page.url}")
            except Exception as e:
                print(f"  ❌ 翻页失败: {e}")

        print()

        # ============ 总结 ============
        print("=" * 60)
        print("  ✅ PATENTSCOPE 全流程测试完成")
        print("=" * 60)
        print(f"  搜索成功: ✅")
        print(f"  结果数: {count_text}")
        print(f"  当前页条目: {len(result_items)}")
        print(f"  详情提取: {'✅' if detail.get('title') else '❌'}")
        print(f"  返回结果: {'✅' if back_ok else '❌'}")
        print(f"  分页: {'✅ 可用' if has_next else '⚠️ 只有一页'}")

        print()
        print("  按 Enter 关闭浏览器...")
        input()

        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
