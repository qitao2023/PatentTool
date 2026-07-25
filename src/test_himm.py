"""
HimmPat 连接测试脚本 — 测试登录、页面跳转、元素定位
用法: python -m src.test_himm
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.browser_manager import BrowserManager
from src.web_automation.authenticator import Authenticator
from src.web_automation.human_behavior import HumanBehavior


async def test():
    settings = Settings()
    print("=" * 60)
    print("HimmPat 连通性测试")
    print(f"  搜索页URL: {settings.himmpat_search_url}")
    print(f"  登录模式: {settings.himmpat_login_mode}")
    print(f"  有账号: {bool(settings.himmpat_username)}")
    print("=" * 60)

    # 1. 启动浏览器
    print("\n[1/7] 启动浏览器...")
    browser = BrowserManager(settings)
    try:
        context, page = await browser.launch_with_retry()
        print("  ✅ 浏览器启动成功")
    except Exception as e:
        print(f"  ❌ 浏览器启动失败: {e}")
        return

    try:
        # 2. 当前页面URL
        print(f"\n[2/7] 当前页面URL: {page.url}")

        # 3. 登录
        print("\n[3/7] 尝试登录...")
        auth = Authenticator(page, settings)
        logged_in = await auth.check_login()
        print(f"  check_login结果: {'已登录' if logged_in else '未登录'}")

        if not logged_in:
            if settings.himmpat_login_mode == "auto":
                print("  尝试自动登录...")
                logged_in = await auth.auto_login()
                print(f"  auto_login结果: {'✅ 成功' if logged_in else '❌ 失败'}")
            if not logged_in:
                print("  引导手动登录...")
                await auth.manual_login()

        print(f"\n[4/7] 登录后URL: {page.url}")
        print(f"  页面标题: {await page.title()}")

        # 5. 跳转到搜索页
        print(f"\n[5/7] 跳转到搜索页...")
        search_url = settings.himmpat_search_url
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            print(f"  ✅ 跳转完成")
        except Exception as e:
            print(f"  ⚠ 跳转过程: {e}")
        print(f"  当前URL: {page.url}")

        # 6. 检测页面元素
        print(f"\n[6/7] 检测页面元素...")

        # 搜索框检测
        input_selectors = [
            ("placeholder*='技术'", "[placeholder*='技术']"),
            ("placeholder*='输入'", "[placeholder*='输入']"),
            ("textarea", "textarea"),
            ("input[type='text']", "input[type='text']"),
            ("任意输入框", "textarea, input[type='text']"),
        ]
        print("\n  --- 搜索框检测 ---")
        found_input = None
        for name, sel in input_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    visible = await el.is_visible()
                    box = await el.bounding_box()
                    ph = await el.get_attribute("placeholder") or ""
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    print(f"  ✅ {name}: <{tag}> visible={visible} "
                          f"y={box['y']:.0f} placeholder='{ph}'")
                    if visible and not found_input:
                        found_input = (name, sel, box)
                else:
                    print(f"  ❌ {name}: 未找到")
            except Exception as e:
                print(f"  ❌ {name}: 错误 - {e}")

        # 按钮检测
        btn_selectors = [
            ("button:检索", "button:has-text('检索')"),
            ("button:搜索", "button:has-text('搜索')"),
            ("[class*=search-btn]", "[class*='search-btn']"),
            ("[class*=search-icon]", "[class*='search-icon']"),
            ("button[type=submit]", "button[type='submit']"),
            ("任意button", "button"),
        ]
        print("\n  --- 搜索按钮检测 ---")
        for name, sel in btn_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    visible = await el.is_visible()
                    text = await el.text_content() or ""
                    print(f"  ✅ {name}: visible={visible} text='{text.strip()}'")
                else:
                    print(f"  ❌ {name}: 未找到")
            except Exception as e:
                print(f"  ❌ {name}: 错误 - {e}")

        # 登录按钮检测
        print("\n  --- 登录按钮检测 ---")
        for sel in ["a[href*='login']", "button:has-text('登录')"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    visible = await el.is_visible()
                    print(f"  {'⚠' if visible else ' '} {sel}: "
                          f"visible={visible}（{'未登录' if visible else '已登录'}）")
                else:
                    print(f"  ✅ {sel}: 不存在（可能已登录）")
            except Exception:
                pass

        # 页面大小
        body_text = await page.evaluate("document.body?.innerText?.length || 0")
        print(f"\n  页面文本长度: {body_text} 字符")

        # 7. 测试输入
        print(f"\n[7/7] 输入测试...")
        if found_input:
            name, sel, box = found_input
            print(f"  向 {name} 输入测试文字...")
            human = HumanBehavior(settings)
            await human.human_type(page, sel, "测试检索式")
            await asyncio.sleep(0.5)
            # 清除输入
            await page.click(sel)
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Delete")
            print(f"  ✅ 输入/清除测试通过")
        else:
            print(f"  ⚠ 未找到可用的输入框，跳过输入测试")

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n" + "=" * 60)
        print("测试完成，5秒后关闭浏览器...")
        await asyncio.sleep(5)
        await browser.close()
        print("浏览器已关闭")


if __name__ == "__main__":
    asyncio.run(test())
