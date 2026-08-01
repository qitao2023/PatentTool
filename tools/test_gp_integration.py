"""
双引擎下载集成测试 — 真实调用 fetch_details_parallel

验证：
  1. search_source 引擎分发：
     google → 全部 Google 下载（✓G 免浏览器，无全文即失败）
     wipo   → 全部 PATENTSCOPE 浏览器（原行为）
  2. 输出 JSON 兼容现有格式（fetch_status=ok, _source=google_patents）
  3. 断点续传（已存在的有效文件跳过）

用法:
  python tools/test_gp_integration.py [wipo|google]
"""
import asyncio
import json
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                   errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import Settings
from src.web_automation.human_behavior import HumanBehavior


class FakeSignals:
    """模拟 WorkerSignals：log/progress 是带 .emit() 的信号对象"""

    def __init__(self):
        self.logs = []

    @property
    def log(self):
        def emit(level, msg):
            self.logs.append((level, msg))
            print(f"[{level}] {msg}")
        emit.emit = emit
        return emit

    @property
    def progress(self):
        def emit(*a, **k):
            pass
        emit.emit = emit
        return emit


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "google"
    settings = Settings()
    out_dir = Path(f"data/test_gp_integration_{mode}")

    # 强制使用指定引擎（不改 yaml，直接覆盖 settings 内部状态）
    settings._raw.setdefault("search", {})["search_source"] = mode
    assert settings.search_source == mode

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, channel="msedge",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        try:
            from src.web_automation.patentscope_scraper import PatentscopeScraper
            scraper = PatentscopeScraper(page, settings, HumanBehavior(settings))
            signals = FakeSignals()

            patents = [
                {"doc_id": "CN116110953", "publication_number": "CN116110953A",
                 "applicant": "中国科学院微电子研究所"},
                {"doc_id": "CN112716751", "publication_number": "CN112716751A",
                 "applicant": "测试申请人"},
                {"doc_id": "CN202410000001", "publication_number": "CN202410000001A",
                 "applicant": "不存在的号"},
            ]

            print("=" * 60)
            print(f"模式: {mode}")
            print("=" * 60)
            n = await scraper.fetch_details_parallel(
                patents, str(out_dir), concurrency=2, signals=signals)

            print()
            print("=" * 60)
            print(f"成功抓取: {n} / {len(patents)}")
            print("=" * 60)

            # 校验输出文件
            for f in sorted(out_dir.glob("*.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                src = d.get("_source", "-")
                status = d.get("fetch_status", "?")
                print(f"  {f.name}: status={status} source={src} "
                      f"claims={len(d.get('claims',''))}c")
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
