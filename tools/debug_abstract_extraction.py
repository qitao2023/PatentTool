"""
诊断：检查 PATENTSCOPE 详情页 body 文本中摘要的实际格式。
"""
import asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.human_behavior import HumanBehavior
from src.web_automation.patentscope_scraper import PatentscopeScraper


async def check_url(page, doc_id, label=""):
    """Check a specific URL format."""
    url = f"https://patentscope2.wipo.int/search/zh/detail.jsf?docId={doc_id}"
    print(f"\n[{label}] {url}")
    await page.goto(url, timeout=60000, wait_until="commit")
    try:
        await page.wait_for_selector("h1", timeout=10000)
    except Exception:
        pass
    await asyncio.sleep(2)
    body = await page.evaluate("() => (document.body?.innerText || '').substring(0, 5000)")
    title = await page.evaluate("() => (document.querySelector('h1')?.textContent || '').substring(0, 100)")
    url_current = page.url
    print(f"  当前URL: {url_current[:120]}")
    print(f"  H1: {title[:100]}")
    print(f"  body长度: {len(body)}")
    # Check for abstract keywords
    has_abs = '摘要' in body or 'Abstract' in body
    print(f"  含摘要/Abstract: {has_abs}")
    if has_abs:
        # Show context around abstract
        for kw in ['摘要', 'Abstract']:
            idx = body.find(kw)
            if idx >= 0:
                snippet = body[max(0,idx):idx+200].replace('\n', ' | ')
                print(f"  '摘要'附近: {snippet[:200]}")
                break
    else:
        print(f"  页面文本前300: {body[:300]}")
    return body


async def main():
    settings = Settings()
    mgr = BrowserManager(settings)
    ctx, page = await mgr.launch_with_retry(max_retries=2)

    if await PatentscopeScraper._check_blocked(page):
        print("Initial 403!")

    # Try different ID formats
    ids_to_try = [
        ("CN108365002", "CN+纯数字"),
        ("CN108365002A", "CN+数字+种类码A"),
        ("WO2025009876", "WO格式"),
    ]

    for doc_id, label in ids_to_try:
        body = await check_url(page, doc_id, label)
        if len(body) > 25:
            print(f"  >>> 页面加载成功! 尝试提取摘要...")

            # Try all 5 regex patterns
            for pl, pat in [
                ("ZH模式", r'摘要[\s\S]*?\(ZH\)\s*(.*?)(?:\n\n#|\n相关专利|\n$)'),
                ("EN模式", r'Abstract\n\(EN\)\s*(.*?)(?:\n\n\(ZH\)|\n\n#)'),
                ("摘要EN", r'摘要[\s\S]*?\(EN\)\s*(.*?)(?:\n\n\(FR\)|\n\n\(ZH\)|\n\n#)'),
                ("字段分隔", r'(?:摘要|Abstract)\s*\n+([\s\S]*?)(?=\n(?:申请号|公布号|IPC|申请人|发明人|权利要求|说明书|Claims?|Description|附图|图式|Drawings)\b)'),
                ("兜底", r'(?:摘要|Abstract)\s*[\s\S]*?(?=Claims?|Description|权利要求|说明书|\Z)'),
            ]:
                m = re.search(pat, body, re.DOTALL)
                if m:
                    txt = m.group(1).strip() if m.groups() else m.group(0).strip()
                    if len(txt) > 10:
                        print(f"    [{pl}] OK! {len(txt)} chars: {txt[:150]}...")
                        break
            else:
                print("    ALL patterns failed - saving body to file")
                Path("e:/01-claudecode/PatentTool/tools/debug_body_good.txt").write_text(body, encoding="utf-8")
            break

    await mgr.close()

if __name__ == "__main__":
    asyncio.run(main())
