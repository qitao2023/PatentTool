"""单元验证 _wait_for_results_stable 的 min_change_from 变化检测逻辑。

不依赖真实浏览器/网络，用假的 _count_result_rows 序列模拟切换过程。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.web_automation.patentscope_scraper import PatentscopeScraper


class _FakePage:
    pass


async def _async_noop(*a, **k):
    return None


def _make_scraper(seq):
    s = PatentscopeScraper(_FakePage(), None, None)
    calls = {"i": 0}

    async def count_rows():
        c = seq[min(calls["i"], len(seq) - 1)]
        calls["i"] += 1
        return c

    async def get_total():
        return None

    s._count_result_rows = count_rows
    s._get_total_result_count = get_total
    return s, calls


async def main():
    # ── 用例1: 切换生效（旧10行 → AJAX 清空 → 200行）──
    seq = [10, 10, 10, 0, 200, 200, 200]
    s, _ = _make_scraper(seq)
    with patch("src.web_automation.patentscope_scraper.asyncio.sleep",
               new=_async_noop):
        result = await s._wait_for_results_stable(
            label="切换200", max_wait=15.0, poll_interval=0.6,
            min_change_from=10)
    assert result == 200, f"用例1失败: 期望200, 实际{result}"
    print("用例1 PASS: 切换生效时稳定在 200 行")

    # ── 用例2: 切换未生效（一直是10行）──
    seq = [10, 10, 10, 10, 10, 10]
    s, _ = _make_scraper(seq)
    with patch("src.web_automation.patentscope_scraper.asyncio.sleep",
               new=_async_noop):
        result = await s._wait_for_results_stable(
            label="切换200", max_wait=3.0, poll_interval=0.6,
            min_change_from=10)
    assert result == 10, f"用例2失败: 期望10, 实际{result}"
    print("用例2 PASS: 切换未生效时返回旧行数 10")

    # ── 用例3: 不传 min_change_from（旧行为，直接判定稳定）──
    seq = [10, 10, 10, 200, 200]
    s, _ = _make_scraper(seq)
    with patch("src.web_automation.patentscope_scraper.asyncio.sleep",
               new=_async_noop):
        result = await s._wait_for_results_stable(
            label="默认", max_wait=3.0, poll_interval=0.6)
    assert result in (10, 200), f"用例3失败: 期望10或200, 实际{result}"
    print(f"用例3 PASS: 默认行为正常（返回 {result}）")

    # ── 用例4: 切换后总数少于200（123行也视为变化）──
    seq = [10, 10, 0, 123, 123, 123]
    s, _ = _make_scraper(seq)
    with patch("src.web_automation.patentscope_scraper.asyncio.sleep",
               new=_async_noop):
        result = await s._wait_for_results_stable(
            label="切换200", max_wait=15.0, poll_interval=0.6,
            min_change_from=10)
    assert result == 123, f"用例4失败: 期望123, 实际{result}"
    print("用例4 PASS: 123行同样被识别为切换成功")

    print("\n全部通过 OK")


if __name__ == "__main__":
    asyncio.run(main())
