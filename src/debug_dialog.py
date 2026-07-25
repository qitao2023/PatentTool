"""
Debug: capture full login flow, including "already logged in" dialog
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.human_behavior import HumanBehavior

async def debug():
    settings = Settings()
    bm = BrowserManager(settings)
    context, page = await bm.launch_with_retry()
    human = HumanBehavior(settings)

    # ---------- network logger ----------
    api_calls = []
    async def on_response(resp):
        url = resp.url
        if 'api' in url and 'sentry' not in url:
            try:
                body = await resp.text()
                api_calls.append({'url': url, 'status': resp.status, 'body': body[:300]})
            except:
                api_calls.append({'url': url, 'status': resp.status, 'body': '<error>'})
    page.on("response", on_response)

    # ---------- fill the form ----------
    await page.goto("https://global.himmpat.com/login", wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_load_state("networkidle", timeout=8000)
    await asyncio.sleep(1)
    print(f"Login page loaded: {page.url}")

    # Fill credentials
    username = settings.himmpat_username or ""
    password = settings.himmpat_password or ""
    if not username or not password:
        print("No credentials!")
        await bm.close()
        return

    # Find username input
    await human.human_type(page, "input[type='text']", username)
    await human.human_type(page, "input[type='password']", password)
    print("Credentials filled")

    # Press Enter to submit
    await page.keyboard.press("Enter")
    print("Enter pressed, waiting for response...")
    await asyncio.sleep(3)

    # Check for dialog
    current_url = page.url
    print(f"\nAfter submit URL: {current_url}")

    # Dump all API calls so far
    print(f"\nAPI calls ({len(api_calls)}):")
    for c in api_calls:
        print(f"  [{c['status']}] {c['url']}")
        print(f"    body: {c['body'][:200]}")

    # Check if dialog is visible
    dialog_info = await page.evaluate("""
    () => {
        // Find modal/overlay elements
        const modals = [];
        document.querySelectorAll('[class*="modal"], [class*="dialog"], [class*="overlay"], [class*="popup"], [class*="message-box"], [role="dialog"]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (el.offsetParent !== null && r.width > 50) {
                modals.push({
                    tag: el.tagName,
                    cls: (el.className||'').slice(0,80),
                    text: (el.textContent||'').trim().slice(0,100),
                    y: r.y, w: r.width, h: r.height,
                });
            }
        });
        // Also check for fixed/absolute positioned overlays
        document.querySelectorAll('div[style*="fixed"], div[style*="absolute"]').forEach(el => {
            const r = el.getBoundingClientRect();
            if (el.offsetParent !== null && r.width > 100 && r.height > 50 && r.y < 200) {
                modals.push({
                    tag: el.tagName + '(overlay)',
                    cls: (el.className||'').slice(0,60),
                    text: (el.textContent||'').trim().slice(0,100),
                });
            }
        });
        return modals;
    }
    """)
    print(f"\nDialogs/modals on page ({len(dialog_info)}):")
    for d in dialog_info:
        print(f"  [{d['tag']}] {d.get('text','')[:80]}")
        if d.get('cls'): print(f"    class={d['cls']}")

    # Try clicking confirm
    for s in ["button:has-text('确定')", "button:has-text('继续')",
              "button:has-text('确认')", "[class*='confirm'] button",
              ".el-message-box button", "[class*='message-box'] button",
              "button:has-text('登录')", ".dialog-footer button"]:
        btn = await page.query_selector(s)
        if btn and await btn.is_visible():
            print(f"\nFound button: '{s}' - clicking...")
            box = await btn.bounding_box()
            print(f"  Position: y={box['y']:.0f} x={box['x']:.0f} w={box['width']:.0f} h={box['height']:.0f}")
            await btn.click()
            await asyncio.sleep(3)
            print(f"After click URL: {page.url}")
            break
    else:
        print("\nNo confirm button found")

    # Wait a bit more
    await asyncio.sleep(5)
    print(f"\nFinal URL: {page.url}")

    # Show any new API calls
    new_calls = api_calls[len(api_calls)-len([c for c in api_calls if 'url' in c]):]
    # Actually just show all API calls again for full picture

    await asyncio.sleep(5)
    await bm.close()
    print("\nDone")

if __name__ == "__main__":
    asyncio.run(debug())
