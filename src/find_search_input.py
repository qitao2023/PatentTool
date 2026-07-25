"""Find what search input looks like on HimmPat search page."""
import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.authenticator import Authenticator

async def main():
    settings = Settings()
    bm = BrowserManager(settings)
    context, page = await bm.launch_with_retry()
    auth = Authenticator(page, settings)

    logged_in = await auth.check_login()
    if not logged_in:
        logged_in = await auth.auto_login()
    if not logged_in:
        print("Login failed")
        await bm.close()
        return

    await page.goto("https://global.himmpat.com/intelligence?active=6", timeout=20000)
    await page.wait_for_load_state("networkidle", timeout=15000)
    await asyncio.sleep(3)

    print(f"URL: {page.url}")
    print(f"Title: {await page.title()}")

    # Dump all interactive elements as JSON
    info = await page.evaluate("""
    () => {
        const results = [];

        // inputs
        document.querySelectorAll('input').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 10) results.push({cat:'input', tag:el.tagName, t:el.type, n:el.name, i:el.id, c:(el.className||'').slice(0,50), p:el.placeholder, v:el.offsetParent!==null, y:r.y, x:r.x, w:r.width, h:r.height});
        });

        // textareas
        document.querySelectorAll('textarea').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 10) results.push({cat:'textarea', n:el.name, i:el.id, c:(el.className||'').slice(0,50), p:el.placeholder, v:el.offsetParent!==null, y:r.y, x:r.x, w:r.width, h:r.height});
        });

        // contenteditable
        document.querySelectorAll('[contenteditable]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 20) results.push({cat:'ce', tag:el.tagName, i:el.id, c:(el.className||'').slice(0,50), v:el.offsetParent!==null, y:r.y, x:r.x, w:r.width, h:r.height, t:(el.textContent||'').slice(0,30)});
        });

        // role=textbox
        document.querySelectorAll('[role=textbox]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width > 20) results.push({cat:'role-tb', tag:el.tagName, i:el.id, c:(el.className||'').slice(0,50), v:el.offsetParent!==null, y:r.y, x:r.x, w:r.width, h:r.height});
        });

        // All visible divs that might be the search area (sorted by y position)
        const allDivs = Array.from(document.querySelectorAll('div')).filter(el => {
            const r = el.getBoundingClientRect();
            return el.offsetParent !== null && r.width > 100 && r.height > 30 && r.y >= 50 && r.y < 600;
        });

        // Focus on divs with text that might be the search placeholder
        allDivs.forEach(el => {
            const txt = (el.textContent || '').trim();
            if (txt.includes('技术') || txt.includes('搜索') || txt.includes('检索') || txt.includes('输入') || txt.includes('专利') || txt.includes('and') || txt.includes('or') || txt.includes('AND') || txt.includes('OR')) {
                const r = el.getBoundingClientRect();
                results.push({cat:'div-text', t:txt.slice(0,40), c:(el.className||'').slice(0,50), y:r.y, x:r.x, w:r.width, h:r.height});
            }
            // divs with cursor/text editing styles
            const cs = window.getComputedStyle(el);
            if (cs.cursor === 'text' || el.getAttribute('contenteditable') !== null) {
                const r = el.getBoundingClientRect();
                results.push({cat:'div-cursor-text', t:txt.slice(0,30), c:(el.className||'').slice(0,50), y:r.y, x:r.x, w:r.width, h:r.height});
            }
        });

        return results;
    }
    """)

    print(f"\nFound {len(info)} potential elements:")
    for e in info:
        print(f"  [{e['cat']}] y={e['y']:.0f} x={e['x']:.0f} w={e['w']:.0f} h={e['h']:.0f}  v={e.get('v','?')}", end="")
        if e.get('tag'): print(f" <{e['tag']}>", end="")
        if e.get('t'): print(f" text=\"{e['t']}\"", end="")
        if e.get('c'): print(f" cls=\"{e['c']}\"", end="")
        if e.get('p'): print(f" ph=\"{e['p']}\"", end="")
        print()

    await asyncio.sleep(5)
    await bm.close()

if __name__ == "__main__":
    asyncio.run(main())
