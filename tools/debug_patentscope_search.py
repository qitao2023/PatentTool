"""
诊断脚本：分析 PATENTSCOPE 搜索结果页，找出为什么只返回10条而非357条。
"""
import asyncio
import sys
sys.path.insert(0, "e:/01-claudecode/PatentTool")

from playwright.async_api import async_playwright

QUERY = "IGZO AND (背栅 OR back gate OR bottom gate) AND (顶栅 OR top gate)"
PROXY = "http://127.0.0.1:7892"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 可见模式方便观察
            proxy={"server": PROXY} if PROXY else None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()

        # ── 1. 搜索 ──────────────────────────────────────────────
        print("=" * 60)
        print("Step 1: 访问搜索页并提交检索式...")
        await page.goto("https://patentscope2.wipo.int/search/zh/search.jsf",
                        timeout=90000, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(3)

        # 填表提交
        await page.evaluate(f'''(query) => {{
            var input = document.getElementById("simpleSearchForm:fpSearch:input");
            if (!input) return "no input";
            input.value = query;
            input.dispatchEvent(new Event("input", {{bubbles: true}}));
            input.dispatchEvent(new Event("change", {{bubbles: true}}));
            var buttons = document.querySelectorAll("button");
            for (var i = 0; i < buttons.length; i++) {{
                if (buttons[i].id && buttons[i].id.indexOf("fpSearch") >= 0) {{
                    buttons[i].click();
                    return "clicked";
                }}
            }}
            return "no button";
        }}''', QUERY)

        # 等待结果
        print("  等待搜索结果...")
        try:
            await page.wait_for_url("**/result.jsf*", timeout=90000)
        except Exception as e:
            print(f"  URL等待超时: {e}")
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(3)
        print(f"  当前URL: {page.url}")
        print(f"  DOM就绪")

        # ── 2. 检查初始结果 ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 2: 检查初始搜索结果（默认每页条数）...")
        await check_page_state(page)

        # ── 3. 改变每页条数 ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 3: 改变每页条数为最大值...")
        result = await page.evaluate('''() => {
            var ids = [
                "resultListCommandsForm:perPage:input",
                "settingsForm:lengthOption:input",
            ];
            for (var i = 0; i < ids.length; i++) {
                var el = document.getElementById(ids[i]);
                if (!el || !el.options) continue;
                console.log("Found dropdown:", ids[i], "options:", el.options.length, "current:", el.value);
                var maxOpt = null, maxVal = null;
                for (var j = 0; j < el.options.length; j++) {
                    var v = parseInt(el.options[j].value, 10);
                    if (!isNaN(v) && (!maxOpt || v > maxOpt)) {
                        maxOpt = v;
                        maxVal = el.options[j].value;
                    }
                }
                if (maxVal && maxOpt > parseInt(el.value, 10)) {
                    el.value = maxVal;
                    el.dispatchEvent(new Event("change", {bubbles: true}));
                    return "ok: " + el.value;
                }
            }
            return "not found";
        }''')
        print(f"  下拉框修改结果: {result}")

        if result and result.startswith("ok"):
            print("  等待页面重新加载...")
            # ⚠️ 关键：等待页面完全重新加载
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
                print("  networkidle 就绪")
            except Exception as e:
                print(f"  networkidle 超时: {e}")
            await asyncio.sleep(5)  # 额外渲染时间
            print("  重新加载完成")

        # ── 4. 检查修改后的结果 ────────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 4: 检查每页条数修改后的结果...")
        await check_page_state(page)

        # ── 5. 详细分析分页控件 ──────────────────────────────────────
        print("\n" + "=" * 60)
        print("Step 5: 详细分析分页控件...")
        paginator_info = await page.evaluate('''() => {
            var info = {};
            // 查找所有 a 标签中可能是分页的元素
            var allLinks = document.querySelectorAll("a");
            var pageLinks = [];
            for (var i = 0; i < allLinks.length; i++) {
                var a = allLinks[i];
                var t = (a.textContent || "").trim();
                var cls = (a.className || "").toString();
                var id = (a.id || "").toString();
                var href = (a.href || "").toString();
                // 过滤出可能是分页的链接
                if (t === "Next" || t === ">" || t === ">>" || t.match(/^\d+$/) ||
                    id.match(/next|Next|nextPage|pageNav/i) ||
                    cls.match(/paginator|pagination|pageNav|next/i) ||
                    href.match(/next|Next|pageNum/i)) {
                    pageLinks.push({
                        text: t.substring(0, 30),
                        id: id.substring(0, 60),
                        cls: cls.substring(0, 100),
                        href: href.substring(0, 200),
                        visible: a.offsetParent !== null
                    });
                }
            }
            info.page_links = pageLinks.slice(0, 30);
            info.total_links_found = pageLinks.length;

            // 查找所有 button 中可能是分页的
            var allButtons = document.querySelectorAll("button");
            var pageButtons = [];
            for (var j = 0; j < allButtons.length; j++) {
                var b = allButtons[j];
                var bt = (b.textContent || "").trim();
                var bcls = (b.className || "").toString();
                if (bt.match(/Next|next|下一页|^>$|^>>$/) ||
                    bcls.match(/paginator|pagination|pageNav|next/)) {
                    pageButtons.push({
                        text: bt.substring(0, 30),
                        cls: bcls.substring(0, 100),
                        visible: b.offsetParent !== null
                    });
                }
            }
            info.page_buttons = pageButtons.slice(0, 20);

            // 查找包含 "next" 或分页相关 class 的所有元素
            var pagElements = document.querySelectorAll(
                "[class*='ps-paginator'], [class*='pageNav'], [class*='page-nav'], "
                + "[class*='navigation'], [class*='pagination-bar'], [class*='resultNav']"
            );
            info.pag_elements_count = pagElements.length;
            for (var p = 0; p < Math.min(pagElements.length, 3); p++) {
                info["pag_el_" + p] = pagElements[p].outerHTML.substring(0, 1000);
            }

            return info;
        }''')

        print(f"  分页相关元素数: {paginator_info.get('pag_elements_count', 0)}")
        for p in range(3):
            html = paginator_info.get(f'pag_el_{p}', '')
            if html:
                print(f"  pag_el[{p}]: {html[:400]}...")
                print()

        print(f"  分页链接: {paginator_info.get('total_links_found', 0)} 个")
        for l in paginator_info.get('page_links', [])[:20]:
            try:
                print(f"    text={l['text']!r} id={l['id']!r} visible={l['visible']}")
            except Exception:
                pass

        print(f"  分页按钮: {len(paginator_info.get('page_buttons', []))} 个")
        for b in paginator_info.get('page_buttons', [])[:20]:
            try:
                print(f"    text={b['text']!r} cls={b['cls'][:80]!r} visible={b['visible']}")
            except Exception:
                pass

        # ── 6. 用多种方式尝试翻页 ──────────────────────────────
        print("\n" + "=" * 60)
        print("Step 6: 尝试多种翻页方式...")

        # 方式1: Playwright locator
        next_btn = page.locator(
            "a[id*='nextPage'], a[id*='navigationNext'], "
            "a:has-text('Next'), a:has-text('下一页'), "
            "a:has-text('>'), "
            "a.paginator-next, a.pagination-next, "
            ".ui-paginator-next:not(.ui-state-disabled)"
        ).first
        count = await next_btn.count()
        print(f"  方式1 (locator): 匹配={count}")
        if count > 0:
            try:
                cls = (await next_btn.get_attribute("class")) or ""
                txt = (await next_btn.text_content() or "").strip()
                print(f"    class={cls!r} text={txt!r}")
            except Exception:
                pass

        # 方式2: JS 查找
        result = await page.evaluate('''() => {
            // 查找所有可见的 a 和 button
            var elems = document.querySelectorAll("a, button");
            var found = [];
            for (var i = 0; i < elems.length; i++) {
                var el = elems[i];
                var t = (el.textContent || "").trim();
                var cls = (el.className || "").toString();
                var id = (el.id || "").toString();
                var tag = el.tagName;
                // 下一页特征
                var isNext = false;
                var reason = "";
                if (t === "Next" || t === ">" || t === ">>") { isNext = true; reason = "text:" + t; }
                if (id.indexOf("next") >= 0 || id.indexOf("Next") >= 0) { isNext = true; reason = "id:" + id; }
                if (cls.indexOf("next") >= 0 || cls.indexOf("Next") >= 0) { isNext = true; reason = "cls:" + cls.substring(0, 60); }
                if (isNext && el.offsetParent !== null) {
                    found.push({tag: tag, reason: reason, text: t.substring(0, 40),
                                cls: cls.substring(0, 80), id: id.substring(0, 60)});
                }
            }
            return found;
        }''')
        print(f"  方式2 (JS next特征): {len(result)} 个")
        for r in result:
            print(f"    {r!r}")

        # 方式3: 查找任何包含分页功能的容器
        result3 = await page.evaluate('''() => {
            var containers = document.querySelectorAll(
                "[class*='ps-paginator'], [class*='paginator'], [class*='pagination'], "
                + "[class*='pageNav'], [class*='page-nav'], [class*='resultsNav'], "
                + "[class*='results-nav'], [class*='resultNav']"
            );
            var info = [];
            for (var c = 0; c < containers.length; c++) {
                var el = containers[c];
                info.push({
                    tag: el.tagName,
                    id: (el.id || "").substring(0, 60),
                    cls: (el.className || "").toString().substring(0, 120),
                    childCount: el.children.length,
                    text: (el.textContent || "").trim().substring(0, 80),
                    html: el.outerHTML.substring(0, 600)
                });
            }
            return info;
        }''')
        print(f"  方式3 (分页容器): {len(result3)} 个")
        for r in result3:
            try:
                print(f"    <{r['tag']}> id={r['id']!r} cls={r['cls'][:80]!r}")
                print(f"      children={r['childCount']} text={r['text']!r}")
                print(f"      html: {r['html'][:300]!r}")
            except Exception:
                pass

        # ── 6. 截图保存 ──────────────────────────────────────────────
        await page.screenshot(path="e:/01-claudecode/PatentTool/tools/debug_screenshot.png",
                              full_page=False)
        print("\n截图已保存: tools/debug_screenshot.png")

        print("\n" + "=" * 60)
        print("按 Enter 关闭浏览器...")
        input()
        await browser.close()


async def check_page_state(page):
    """打印当前页面的关键状态"""
    state = await page.evaluate('''() => {
        var info = {};
        // 结果行
        var rows = document.querySelectorAll("tr.trans-result-list-row");
        info.trans_result_rows = rows.length;
        var psResults = document.querySelectorAll(".ps-patent-result");
        info.ps_patent_result = psResults.length;
        // 结果计数
        var countEl = document.querySelector(".results-count, [class*='result-count'], [class*='resultCount']");
        info.result_count_text = countEl ? countEl.textContent.trim() : "N/A";
        // 页面标题/提示
        var bodyText = (document.body && document.body.innerText) || "";
        // 查找包含数字的"条"或"results"文本
        var matches = bodyText.match(/(\d[\d,]*)\s*(条|results|条结果|results found)/i);
        info.count_from_text = matches ? matches[0] : "N/A";
        // 分页信息
        var paginators = document.querySelectorAll(".ui-paginator, [class*='paginator'], [class*='pagination']");
        info.paginator_count = paginators.length;
        for (var k = 0; k < Math.min(paginators.length, 3); k++) {
            info["paginator_" + k] = paginators[k].textContent.trim().substring(0, 200);
        }
        // 当前每页条数
        var perPageEl = document.getElementById("resultListCommandsForm:perPage:input")
                     || document.getElementById("settingsForm:lengthOption:input");
        info.current_per_page = perPageEl ? perPageEl.value : "N/A";
        // URL
        info.url = window.location.href;
        // 前5行的标题
        var titles = [];
        var titleEls = document.querySelectorAll(".ps-patent-result--title--title, .trans-result-list-row .ps-patent-result--title--title");
        for (var t = 0; t < Math.min(titleEls.length, 5); t++) {
            titles.push(titleEls[t].textContent.trim().substring(0, 80));
        }
        info.sample_titles = titles;
        return info;
    }''')

    print(f"  当前URL: {state.get('url', 'N/A')}")
    print(f"  结果计数文本: {state.get('result_count_text', 'N/A')}")
    print(f"  文本中匹配的条数: {state.get('count_from_text', 'N/A')}")
    print(f"  tr.trans-result-list-row: {state.get('trans_result_rows', 0)}")
    print(f"  .ps-patent-result: {state.get('ps_patent_result', 0)}")
    print(f"  当前每页条数: {state.get('current_per_page', 'N/A')}")
    print(f"  分页组件数: {state.get('paginator_count', 0)}")
    for k in range(3):
        txt = state.get(f'paginator_{k}', '')
        if txt:
            print(f"  分页[{k}]: {txt}")
    sample = state.get('sample_titles', [])
    if sample:
        print(f"  前5条标题:")
        for t in sample:
            print(f"    - {t}")


if __name__ == "__main__":
    asyncio.run(main())
