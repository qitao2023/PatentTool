"""
CNIPA PSS Recon v8 - Correct flow: PSS -> auto-redirect to SSO -> login -> back to PSS
This mimics the user's manual browser experience.
"""
import asyncio
import sys
import io
import json
import re
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright


CNIPA_PSS_SEARCH = "https://pss-system.cponline.cnipa.gov.cn/conventionalSearch"
PROFILE_DIR = Path.cwd() / "profiles" / "cnipa_browser"


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


async def wait_and_report(page, max_wait: int = 60) -> int:
    """Wait for page content, report every 5s. Returns final body length."""
    for i in range(max_wait + 1):
        await asyncio.sleep(1)
        try:
            body_len = await page.evaluate("document.body?.innerText?.length || 0")
            html_len = await page.evaluate("document.body?.innerHTML?.length || 0")
            title = await page.title()
            url = page.url[:120]
            if i % 5 == 0:
                log(f"  [{i}s] url={url} title='{title[:50]}' body={body_len} html={html_len}")
            if body_len > 100:
                log(f"  [OK] Page loaded! body={body_len}")
                return body_len
        except Exception as e:
            if i % 5 == 0:
                log(f"  [{i}s] evaluate error: {e}")
    return 0


async def analyze(page, label: str) -> dict:
    """Full page analysis"""
    info = await page.evaluate("""() => {
        const r = {};
        r.url = window.location.href;
        r.title = document.title;
        r.bodyLen = (document.body?.innerText || '').length;
        r.htmlLen = (document.body?.innerHTML || '').length;

        r.iframes = [];
        document.querySelectorAll('iframe').forEach(f => {
            const rect = f.getBoundingClientRect();
            r.iframes.push({src: (f.src||'').slice(0,200), id: f.id||'', name: f.name||'',
                w: Math.round(rect.width), h: Math.round(rect.height),
                vis: rect.width > 50 && f.offsetParent !== null});
        });

        r.inputs = [];
        document.querySelectorAll('input:not([type="hidden"]), textarea, [contenteditable="true"]').forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width > 30 && rect.height > 10) {
                const lbl = (el.labels?.[0]?.textContent || '').trim().slice(0,50);
                r.inputs.push({id: el.id||'', name: el.name||'', type: el.type||'',
                    ph: el.placeholder||'', label: lbl,
                    y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height),
                    vis: el.offsetParent !== null});
            }
        });

        r.buttons = [];
        document.querySelectorAll('button, a.btn, [role="button"], [class*="btn"]').forEach(el => {
            const rect = el.getBoundingClientRect();
            const txt = (el.textContent||el.value||'').trim();
            if (rect.width > 15 && rect.width < 500 && txt && txt.length < 60)
                r.buttons.push({text: txt.slice(0,50), id: el.id||'', y: Math.round(rect.y),
                    w: Math.round(rect.width), vis: el.offsetParent !== null});
        });

        r.tabs = [];
        document.querySelectorAll('[class*="tab"], [role="tab"], .el-tabs__item').forEach(el => {
            const txt = (el.textContent||'').trim();
            const rect = el.getBoundingClientRect();
            if (rect.width > 30 && txt.length > 1 && txt.length < 30)
                r.tabs.push({text: txt.slice(0,30), y: Math.round(rect.y)});
        });

        r.tables = [];
        document.querySelectorAll('table').forEach((el,i) => {
            const rect = el.getBoundingClientRect();
            const rows = el.querySelectorAll('tr').length;
            const ths = Array.from(el.querySelectorAll('th')).map(h => h.textContent.trim()).slice(0,10);
            if (rect.width > 100 && rows > 0)
                r.tables.push({i, rows, cols: ths.length, headers: ths, y: Math.round(rect.y)});
        });

        r.pagination = [];
        document.querySelectorAll('[class*="pagination"], [class*="pager"]').forEach(el => {
            const txt = (el.textContent||'').trim().slice(0,80);
            if (txt) r.pagination.push({text: txt, y: Math.round(el.getBoundingClientRect().y)});
        });

        const t = document.body?.innerText || '';
        r.feat = {
            search: /检索|搜索|查询/.test(t), patentNum: /申请号|公开号|专利号/.test(t),
            keyword: /关键词|发明名称/.test(t), applicant: /申请人|专利权人/.test(t),
            inventor: /发明人/.test(t), ipc: /IPC|分类号/.test(t),
            login: /登录|密码|验证码/.test(t), captcha: /验证码|滑块/.test(t),
            cnCount: (t.match(/CN\\d{4,}[A-Z]?/g)||[]).length,
            tableSearch: /表格检索|高级检索|常规检索/.test(t),
        };
        return r;
    }""")
    return info


def print_elems(info: dict):
    for inp in info.get('inputs', [])[:20]:
        log(f"    INP id={inp['id']} name={inp['name']} type={inp['type']} "
            f"ph='{inp['ph']}' label='{inp['label']}' y={inp['y']} {inp['w']}x{inp['h']} vis={inp['vis']}")
    for btn in info.get('buttons', [])[:25]:
        if btn['vis']: log(f"    BTN '{btn['text']}' id={btn['id']} y={btn['y']}")
    for tab in info.get('tabs', [])[:15]:
        log(f"    TAB '{tab['text']}' y={tab['y']}")
    for t in info.get('tables', []):
        log(f"    TBL rows={t['rows']} cols={t['cols']} hdrs={t['headers']} y={t['y']}")
    for p in info.get('pagination', []):
        log(f"    PAGE '{p['text']}' y={p['y']}")
    for f in info.get('iframes', []):
        log(f"    FRAME id={f['id']} name={f['name']} vis={f['vis']} {f['w']}x{f['h']} src={f['src'][:150]}")


async def main():
    log("=" * 70)
    log("  CNIPA PSS Recon v8 - Correct Redirect Flow")
    log(f"  Target: {CNIPA_PSS_SEARCH}")
    log("=" * 70)

    pw = await async_playwright().start()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="msedge", headless=False,
        locale="zh-CN", timezone_id="Asia/Shanghai",
        ignore_https_errors=True, no_viewport=True,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled", "--no-proxy-server"],
    )

    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get:()=>undefined});
        Object.defineProperty(navigator, 'plugins', {get:()=>{
            const p=[{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}];
            p.item=i=>p[i]; p.length=p.length; return p;
        }});
        Object.defineProperty(navigator, 'languages', {get:()=>['zh-CN','zh','en-US','en']});
    """)

    page = await context.new_page()
    page.set_default_timeout(30000)
    page.on("dialog", lambda d: d.accept())

    all_data = {}

    try:
        # ================================================================
        # KEY: Go to PSS SEARCH FIRST (mimics user typing the URL)
        # It will auto-redirect to SSO login, then back to search after login
        # ================================================================
        log("\n[Step 1] Navigating to PSS search page FIRST...")
        log("  (This triggers: PSS -> redirect to SSO -> login -> redirect back)")
        try:
            await page.goto(CNIPA_PSS_SEARCH, wait_until="commit", timeout=30000)
        except Exception as e:
            log(f"  Initial nav issue: {str(e)[:100]}")

        await asyncio.sleep(2)
        log(f"  Current URL: {page.url[:150]}")

        # ================================================================
        # Wait for redirect chain to settle, detect where we land
        # ================================================================
        log("\n[Step 2] Following redirect chain...")
        last_url = ""
        for i in range(10):
            await asyncio.sleep(1)
            try:
                cur = page.url[:150]
            except Exception:
                continue
            if cur != last_url:
                log(f"  [{i}s] {cur}")
                last_url = cur

        # Analyze where we are
        cur_url = page.url.lower()
        body_len = await page.evaluate("document.body?.innerText?.length || 0")
        log(f"\n  Landed at: {page.url[:150]}")
        log(f"  Body text: {body_len} chars")

        if body_len > 100:
            # Already have content - maybe cached login
            log("  [OK] Already have page content!")
            sinfo = await analyze(page, "SEARCH")
            all_data["search"] = sinfo
            print_elems(sinfo)
        elif "login" in cur_url or "tysf" in cur_url or "sso" in cur_url:
            # On login page - need to log in
            log("\n  On login page. Analyzing...")
            linfo = await analyze(page, "LOGIN")
            all_data["login"] = linfo
            print_elems(linfo)

            log("\n  >>> PLEASE LOG IN NOW <<<")
            log("  Complete login (including captcha) in the browser window.")
            log("  After login, SSO will redirect back to PSS search.")
            log("  Script auto-detects PSS search page...")

            # Wait for redirect to PSS search after login
            for i in range(120):
                await asyncio.sleep(1)
                try:
                    cur = page.url.lower()
                    body = await page.evaluate("document.body?.innerText?.length || 0")
                except Exception:
                    continue

                if "pss-system" in cur and body > 50:
                    log(f"\n  [OK] Reached PSS search! ({i}s) body={body}")
                    break

                if i % 15 == 0:
                    log(f"  [{i}s] url={cur[:100]} body={body}")

                # Detect login in progress
                if i == 5:
                    body_check = await page.evaluate("document.body?.innerText?.slice(0,200) || ''")
                    if "登录" in body_check:
                        log("  Still on login page - please complete login...")
            else:
                log("  Timeout waiting for PSS redirect. Checking current state...")

        # ================================================================
        # Final analysis of current page (should be PSS search)
        # ================================================================
        log(f"\n[Step 3] Final analysis of current page...")
        log(f"  URL: {page.url[:150]}")

        body_len = await wait_and_report(page, max_wait=30)

        if body_len > 100:
            sinfo = await analyze(page, "SEARCH" if "search" in page.url.lower() else "CURRENT")
            all_data["final"] = sinfo
            log("\n  --- Page Elements ---")
            print_elems(sinfo)

            # Try test search
            if sinfo.get('feat', {}).get('search') or sinfo.get('inputs'):
                log("\n[Step 4] Test search...")
                test_pn = "CN202410000001A"
                filled = False

                # Score inputs
                scored = []
                for inp in sinfo.get('inputs', []):
                    if not inp['vis']: continue
                    s = 0
                    txt = (inp.get('ph','') + inp.get('label','')).lower()
                    if any(kw in txt for kw in ['申请号','公开号','专利号']): s += 10
                    if any(kw in txt for kw in ['检索','搜索']): s += 3
                    scored.append((s, inp))
                scored.sort(key=lambda x: -x[0])

                for score, inp in scored[:5]:
                    sel = f"#{inp['id']}" if inp['id'] else f"[name='{inp['name']}']" if inp['name'] else None
                    if not sel: continue
                    try:
                        loc = page.locator(sel).first
                        if await loc.count() > 0:
                            await loc.click(); await asyncio.sleep(0.2)
                            await loc.fill(test_pn)
                            log(f"  Filled {sel}: '{inp.get('ph','')}' (score={score})")
                            filled = True; break
                    except Exception: continue

                if filled:
                    # Click search
                    for btn in sinfo.get('buttons', []):
                        if btn['vis'] and any(kw in btn['text'] for kw in ['检索','搜索']):
                            try:
                                await page.locator(f"button:has-text('{btn['text']}')").first.click()
                                log(f"  Clicked: '{btn['text']}'"); break
                            except Exception: continue
                    else:
                        await page.keyboard.press("Enter")

                    await asyncio.sleep(5)
                    body = await page.evaluate("document.body?.innerText?.slice(0,5000) || ''")
                    cn = len(re.findall(r'CN\d{4,}[A-Z]?', body))
                    log(f"  Search results: {cn} CN numbers, {len(body)} chars")
                    if body:
                        log(f"  Preview: {body[:500]}")

        else:
            log("  [WARN] Page body is empty!")
            log("  Check the browser window manually.")
            log("  Can you see the search page? If so, CNIPA may be blocking automation.")
            all_data["final"] = {"url": page.url, "bodyLen": body_len, "note": "page empty"}

        # ================================================================
        # Save outputs
        # ================================================================
        log("\n[Step 5] Saving outputs...")
        data_dir = Path.cwd() / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        try:
            await page.screenshot(path=str(data_dir / "cnipa_final.png"), full_page=True)
            log("  Screenshot saved")
        except Exception as e:
            log(f"  Screenshot error: {e}")

        html = await page.content()
        (data_dir / "cnipa_final.html").write_text(html, encoding="utf-8")

        body = await page.evaluate("document.body?.innerText || ''")
        (data_dir / "cnipa_body.txt").write_text(body, encoding="utf-8")

        (data_dir / "cnipa_analysis.json").write_text(
            json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # ================================================================
        # Summary
        # ================================================================
        log("\n" + "=" * 70)
        log("  SUMMARY")
        log("=" * 70)
        for k, v in all_data.items():
            if isinstance(v, dict):
                feats = v.get('feat', {})
                log(f"  [{k}] body={v.get('bodyLen',0)} inputs={len(v.get('inputs',[]))} "
                    f"btns={len(v.get('buttons',[]))} iframes={len(v.get('iframes',[]))}")
                if feats:
                    log(f"    features: {json.dumps(feats, ensure_ascii=False)}")

        if body:
            log(f"\n  Body preview (first 800 chars):")
            log(body[:800])

    except Exception as e:
        log(f"[ERROR] {e}")
        import traceback; traceback.print_exc()
    finally:
        log(f"\n{'='*70}")
        log("Browser stays open 60s. Check the PSS search page!")
        log("=" * 70)
        await asyncio.sleep(60)
        await context.close()
        await pw.stop()
        log("Done.")


if __name__ == "__main__":
    asyncio.run(main())
