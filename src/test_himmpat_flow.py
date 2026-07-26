"""
HimmPat 全流程测试脚本
用法: python -m src.test_himmpat_flow [检索式]

测试流程:
  1. 启动浏览器 → 登录
  2. 输入检索式 → 点击检索
  3. 检查结果页结构（分页、条目数）
  4. 逐条点击专利 → 提取详情 → 返回
  5. 翻页 → 重复
  6. 汇总统计
"""
import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.authenticator import Authenticator
from src.web_automation.human_behavior import HumanBehavior
from src.web_automation.scraper import HimmPatScraper


async def test_scraper(query: str | None = None):
    """测试完整流程"""
    settings = Settings()

    if not query:
        query = 'TA=( "人脸识别" AND "深度学习" ) AND IPC=(G06K9 OR G06V)'

    print("=" * 70)
    print("  HimmPat 全流程测试")
    print(f"  检索式: {query}")
    print(f"  搜索页: {settings.himmpat_search_url}")
    print(f"  登录模式: {settings.himmpat_login_mode}")
    print("=" * 70)

    # ====== 1. 启动浏览器 ======
    print("\n[1/6] 启动浏览器...")
    browser = BrowserManager(settings)
    try:
        context, page = await browser.launch_with_retry(max_retries=2)
        print("  [OK] 浏览器启动成功")
    except Exception as e:
        print(f"  [FAIL] 启动失败: {e}")
        return

    try:
        # ====== 2. 登录 ======
        print("\n[2/6] 检查/执行登录...")
        auth = Authenticator(page, settings)
        logged_in = await auth.check_login()

        if not logged_in:
            print("  未登录，尝试自动登录...")
            if settings.himmpat_login_mode == "auto":
                logged_in = await auth.auto_login()
                print(f"  自动登录: {'[OK] 成功' if logged_in else '[FAIL] 失败'}")
            if not logged_in:
                print("  请手动登录（在浏览器中操作）...")
                await auth.manual_login()
        else:
            print("  [OK] 已登录")

        print(f"  当前URL: {page.url}")

        # ====== 3. 导航到搜索页 + 分析页面结构 ======
        print("\n[3/6] 分析搜索页结构...")
        search_url = settings.himmpat_search_url
        if search_url not in page.url:
            await page.goto(search_url, timeout=20000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await asyncio.sleep(2)

        # Dump 搜索页元素
        page_info = await page.evaluate("""() => {
            const info = {};

            // 搜索输入框
            const inputs = [];
            document.querySelectorAll('textarea, input[type="text"], [contenteditable="true"], [role="textbox"]').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width > 50) {
                    inputs.push({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        id: el.id || '',
                        className: (el.className || '').slice(0, 60),
                        placeholder: el.placeholder || '',
                        contenteditable: el.getAttribute('contenteditable') || '',
                        y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
                        visible: el.offsetParent !== null
                    });
                }
            });
            info.searchInputs = inputs;

            // 搜索按钮
            const buttons = [];
            document.querySelectorAll('button, [role="button"], a.btn, span[class*="search"]').forEach(el => {
                const t = (el.textContent || '').trim();
                const r = el.getBoundingClientRect();
                if ((t.includes('检索') || t.includes('搜索') || el.className?.includes?.('search'))
                    && r.width > 20 && r.width < 400) {
                    buttons.push({
                        tag: el.tagName.toLowerCase(),
                        text: t.slice(0, 30),
                        className: (el.className || '').slice(0, 60),
                        y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
                    });
                }
            });
            info.searchButtons = buttons;

            // 页面标题
            info.pageTitle = document.title || '';
            info.bodyLength = (document.body?.innerText || '').length;

            return info;
        }""")

        print(f"  页面标题: {page_info.get('pageTitle', '?')}")
        print(f"  页面文本长度: {page_info.get('bodyLength', 0)} 字符")
        print(f"\n  搜索输入框 ({len(page_info.get('searchInputs', []))} 个):")
        for inp in page_info.get('searchInputs', [])[:5]:
            print(f"    <{inp['tag']}> id={inp['id']} cls={inp['className'][:40]} "
                  f"ph='{inp['placeholder']}' y={inp['y']} {inp['w']}x{inp['h']} "
                  f"vis={inp['visible']} ce={inp['contenteditable']}")
        print(f"\n  搜索按钮 ({len(page_info.get('searchButtons', []))} 个):")
        for btn in page_info.get('searchButtons', [])[:5]:
            print(f"    <{btn['tag']}> text='{btn['text']}' cls={btn['className'][:40]} "
                  f"y={btn['y']} {btn['w']}x{btn['h']}")

        # ====== 4. 执行检索 ======
        print(f"\n[4/6] 执行检索: {query[:80]}...")
        human = HumanBehavior(settings)
        scraper = HimmPatScraper(page, settings, human)

        # 单独执行搜索（不抓详情）
        print("  -> 导航+输入检索式...")
        await scraper._navigate_and_search(query, 1)
        print("  -> 等待搜索结果...")
        await scraper._wait_for_results()

        # 检查结果页结构
        print("\n  检查结果页结构...")
        result_info = await page.evaluate("""() => {
            const info = {};

            // 结果统计文本
            const bodyText = (document.body?.innerText || '');
            info.totalText = bodyText.slice(0, 500);

            // 查找结果计数
            const countMatch = bodyText.match(
                /(?:找到|共|约|about|found)\\s*(\\d[\\d,]*)\\s*(?:条|篇|个|结果|result|patent)/i
            );
            info.resultCount = countMatch ? countMatch[1] : '?';

            // 查找专利条目
            const cnMatches = bodyText.match(/CN\\s*\\d{4,}[A-Z]?/g);
            info.cnCount = cnMatches ? new Set(cnMatches.map(s => s.replace(/\\s/g, ''))).size : 0;

            // 检查分页
            const pagination = document.querySelector(
                '.el-pagination, .ant-pagination, [class*="pagination"], [class*="pager"]'
            );
            if (pagination) {
                const pText = pagination.textContent?.trim() || '';
                info.hasPagination = true;
                info.paginationText = pText.slice(0, 200);
            } else {
                info.hasPagination = false;
            }

            // 检查结果列表容器
            const tables = document.querySelectorAll('table');
            info.tableCount = tables.length;
            if (tables.length > 0) {
                const t = tables[0];
                info.firstTableRows = t.querySelectorAll('tr').length;
            }

            return info;
        }""")

        print(f"  结果计数: {result_info.get('resultCount', '?')}")
        print(f"  页面CN专利号数: {result_info.get('cnCount', 0)}")
        print(f"  有分页: {result_info.get('hasPagination', False)}")
        if result_info.get('paginationText'):
            print(f"  分页文本: {result_info['paginationText'][:150]}")
        print(f"  表格数: {result_info.get('tableCount', 0)}")
        if result_info.get('firstTableRows'):
            print(f"  第一个表格行数: {result_info['firstTableRows']}")

        # 查找结果条目
        items = await scraper._find_result_items()
        print(f"\n  找到 {len(items)} 个结果条目:")
        for i, item in enumerate(items[:5]):
            print(f"    [{i}] {item.get('publication_number','?')}: "
                  f"{item.get('title','')[:60]} | "
                  f"row={item.get('rowTag','?')} cls={item.get('rowClass','')[:30]}")

        if len(items) == 0:
            print("\n  [WARN] 未找到结果条目！保存页面文本用于调试...")
            body_text = await page.evaluate("document.body?.innerText?.slice(0,3000) || ''")
            print(f"  页面文本前3000字符:\n{body_text}")
            return

        # ====== 4.5 调试：dump 页面上所有链接 ======
        print(f"\n[4.5] 调试 - 页面上的链接:")
        links_info = await page.evaluate("""() => {
            const links = [];
            document.querySelectorAll('a').forEach(a => {
                const r = a.getBoundingClientRect();
                if (r.width > 10 && r.height > 10) {
                    links.push({
                        text: (a.textContent || '').trim().slice(0, 80),
                        href: (a.href || '').slice(0, 150),
                        y: Math.round(r.y),
                        x: Math.round(r.x),
                        w: Math.round(r.width),
                    });
                }
            });
            return links;
        }""")
        for l in links_info[:30]:
            if l['text'] and len(l['text']) > 2:
                print(f"    y={l['y']} [{l['w']}px] \"{l['text'][:60]}\"")
                print(f"         href={l['href']}")

        # ====== 5. 测试点击第一个专利 ======
        print(f"\n[5/6] 测试点击第一条专利 -> 提取详情 -> 返回...")

        # 用第一个专利号点击提取
        first_pn = items[0].get("publication_number", "")
        first_title = items[0].get("title", "")
        print(f"  目标: {first_pn} \"{first_title[:50]}\"")
        print(f"\n  [DEBUG] 查找标题周围的HTML结构...")
        html_debug = await page.evaluate(f"""(title) => {{
            const results = [];
            // 找包含标题文本的元素
            const all = document.querySelectorAll('*');
            for (const el of all) {{
                const t = (el.textContent || '').trim();
                if (t === title || (t.length > 10 && t.includes(title.slice(0, 20)))) {{
                    const r = el.getBoundingClientRect();
                    if (r.width > 50 && r.height > 15) {{
                        const info = {{
                            tag: el.tagName,
                            id: el.id || '',
                            cls: (el.className || '').slice(0, 80),
                            text: t.slice(0, 100),
                            y: Math.round(r.y), h: Math.round(r.height),
                            w: Math.round(r.width),
                            hasClick: !!el.onclick,
                            cursor: window.getComputedStyle(el).cursor,
                            parentTag: el.parentElement?.tagName || '',
                            parentCls: (el.parentElement?.className || '').slice(0, 60),
                        }};
                        // 找父级和子级链接
                        const childLinks = el.querySelectorAll('a');
                        info.childLinks = childLinks.length;
                        results.push(info);
                    }}
                }}
            }}
            return results.slice(0, 10);
        }}""", first_title)
        for h in html_debug:
            print(f"    <{h['tag']}> id={h['id']} cls={h['cls'][:50]}")
            print(f"       text=\"{h['text'][:80]}\" y={h['y']} {h['w']}x{h['h']} cursor={h['cursor']}")
            print(f"       parent=<{h['parentTag']}> cls={h['parentCls'][:50]} childLinks={h['childLinks']}")

        print(f"\n  [DEBUG] 检查当前URL和新标签页...")
        print(f"  点击前URL: {page.url}")

        # 尝试直接点击 list-item div
        print(f"\n  尝试方案A: 直接点击 list-item div...")
        try:
            item_div = page.locator(f"[class*='list-item-CN-{first_pn.replace('CN','').replace('A','').replace('B','').replace('U','')}']").first
            if await item_div.count() > 0:
                await item_div.click(force=True, timeout=3000)
                await asyncio.sleep(3)
                print(f"  点击后URL: {page.url}")
                await scraper._dismiss_popups()
                detail_text = await page.evaluate("document.body?.innerText?.slice(0,5000) || ''")
                print(f"  点击后页面文本(前3000):\n{detail_text[:3000]}")
                # 返回
                await scraper._go_back_to_results()
        except Exception as e:
            print(f"  方案A异常: {e}")

        print(f"\n  尝试方案B: 用原有方法点击标题...")
        result = await scraper._click_and_extract_one(
            patent_number=first_pn,
            title_hint=first_title,
            query_index=1,
            global_index=1,
        )
        if result:
            print(f"  [OK] 提取成功!")
            print(f"  公开号: {result.get('publication_number', '?')}")
            print(f"  标题: {(result.get('title') or '')[:80]}")
            print(f"  全文长度: {len(result.get('full_text', ''))} 字符")
            print(f"  权利要求长度: {len(result.get('claims', ''))} 字符")
            print(f"  说明书长度: {len(result.get('description', ''))} 字符")
            print(f"  摘要: {(result.get('abstract') or '')[:100]}")
            print(f"  IPC: {result.get('ipc', '?')}")
            print(f"  申请人: {result.get('applicant', '?')}")
        else:
            print("  [FAIL] 提取失败")
            # Dump 当前页面文本用于诊断
            txt = await page.evaluate("document.body?.innerText?.slice(0,2000) || ''")
            print(f"  当前页面文本前2000字符:\n{txt}")

        # 返回
        print("\n  返回结果列表...")
        await scraper._go_back_to_results()
        await asyncio.sleep(2)

        # 检查是否成功返回
        post_back_items = await scraper._find_result_items()
        print(f"  返回后找到 {len(post_back_items)} 个条目")

        # 测试翻页
        print(f"\n  测试翻页...")
        has_next = await scraper._go_to_next_page()
        print(f"  翻页结果: {'[OK] 成功' if has_next else '[FAIL] 已是最后一页或无分页'}")

        if has_next:
            await asyncio.sleep(2)
            page2_items = await scraper._find_result_items()
            print(f"  第2页找到 {len(page2_items)} 个条目")

        # ====== 6. 完整执行（少量） ======
        print(f"\n[6/6] 完整流程测试（最多3条）...")
        # 先回到搜索页重新检索
        await scraper._navigate_and_search(query, 1)
        await scraper._wait_for_results()

        results = await scraper.execute_query(query, query_index=1,
                                              max_results=3)
        print(f"\n  [OK] 完整流程完成: 获取 {len(results)} 篇专利全文")
        for r in results:
            print(f"    {r.get('publication_number','?')}: "
                  f"{(r.get('title') or '')[:60]} | "
                  f"全文{len(r.get('full_text',''))}字 | "
                  f"权利要求{len(r.get('claims',''))}字")

    except Exception as e:
        print(f"\n[FAIL] 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n" + "=" * 70)
        print("测试完成。浏览器保持打开，可手动检查。")
        print("按 Enter 关闭浏览器...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        await browser.close()
        print("浏览器已关闭")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(test_scraper(query))
