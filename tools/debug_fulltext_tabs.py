"""
诊断：查看 WO / JP 专利详情页的 tab 结构和 FULLTEXT 面板文本。
用于修复「权利要求/说明书提取为空」。
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.patentscope_scraper import PatentscopeScraper


async def dump_patent(page, doc_id, label):
    out = Path("e:/01-claudecode/PatentTool/tools/debug_ft.txt")
    url = f"https://patentscope2.wipo.int/search/zh/detail.jsf?docId={doc_id}"
    print(f"\n{'='*70}\n[{label}] {doc_id}")
    print(f"URL: {url}")
    await page.goto(url, timeout=60000, wait_until="commit")
    try:
        await page.wait_for_selector("h1", timeout=10000)
    except Exception:
        pass
    await asyncio.sleep(2)

    # 1) 列出页面上所有 tab 链接
    tabs = await page.evaluate('''() => {
        var out = [];
        document.querySelectorAll("a[href]").forEach(function(a) {
            var h = a.getAttribute("href") || "";
            if (/tab|Tab|detail|FULL|CLAIM|DESC|Abstract|abstract|PDF/i.test(h)) {
                out.push(h + " | text=" + (a.textContent||"").trim().substring(0,30));
            }
        });
        return out.slice(0, 40);
    }''')
    print("Tab 链接:")
    for t in tabs:
        print(f"   {t[:140]}")
    if not tabs:
        # 无 tab：打印 body 前 400 字符诊断（403/未知专利/加载慢）
        body = await page.evaluate(
            "() => (document.body?.innerText || '').substring(0, 400)")
        print(f"  无tab! body前400: {repr(body[:400])}")

    # 2) 尝试各 tab 关键词点击，dump 面板文本
    for kw in ("FULLTEXT", "fulltext", "FULL_TEXT", "PATENTFULLTEXT",
               "PCTCLAIMS", "PCTDESCRIPTION", "DESCRIPTION", "CLAIMS"):
        try:
            tab = page.locator(f"a[href*='{kw}']").first
            if await tab.count() == 0:
                continue
            try:
                await tab.click(timeout=8000)
            except Exception:
                await page.evaluate(
                    f'() => {{ var t = document.querySelector("a[href*=\'{kw}\']"); if(t) t.click(); }}')
            await asyncio.sleep(2)
            text = await page.evaluate('''() => {
                var panels = document.querySelectorAll(".ui-tabs-panel");
                for (var i = 0; i < panels.length; i++) {
                    if (panels[i].style.display !== "none" && panels[i].offsetParent) {
                        return panels[i].textContent;
                    }
                }
                return "";
            }''')
            if text and len(text.strip()) > 50:
                print(f"\n>>> tab[{kw}] 面板文本 {len(text)} 字符")
                # 找 claims/description 标记位置
                for mark in ("[Claim", "[权利要求", "Claim 1", "权利要求书",
                             "Claims", "技术领域", "Description", "说明书",
                             "Abstract", "摘要"):
                    idx = text.find(mark)
                    if idx >= 0:
                        print(f"    标记 '{mark}' @{idx}: {text[idx:idx+120]!r}")
                # 保存面板全文
                with out.open("a", encoding="utf-8") as f:
                    f.write(f"\n{'#'*70}\n[{label}] tab={kw} {len(text)}字符\n{'#'*70}\n")
                    f.write(text[:40000])
                print(f"    已保存 → {out}")
                break  # 拿到一个非空面板就够了
            else:
                print(f"tab[{kw}] 面板为空/未找到")
        except Exception as e:
            print(f"tab[{kw}] 异常: {e}")


async def main():
    settings = Settings()
    # 强制用 firefox（msedge/chrome 已被 403 限流）
    BrowserManager._force_channel = "firefox"
    mgr = BrowserManager(settings)
    ctx, page = await mgr.launch_with_retry(max_retries=2)

    if await PatentscopeScraper._check_blocked(page):
        print("!!! 初始 403")

    # 先建 session
    await page.goto(settings.patentscope_search_url, timeout=60000,
                    wait_until="load")
    await asyncio.sleep(3)

    # 本轮失败样例：US/EP/KR/WO
    await dump_patent(page, "US309780320", "US-失败样例")
    await dump_patent(page, "EP346692069", "EP-失败样例")
    await dump_patent(page, "KR95459910", "KR-失败样例")
    await dump_patent(page, "WO2013103163", "WO-说明书为空样例")

    await mgr.close()
    print("\n完成")


if __name__ == "__main__":
    asyncio.run(main())
