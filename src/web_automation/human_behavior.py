"""
人类行为模拟 - 键盘打字节奏、鼠标移动、页面滚动和延迟生成
"""
import asyncio
import random
import math
from typing import Optional

from src.utils.config import Settings


class HumanBehavior:
    """模拟人类操作的时序和交互模式"""

    def __init__(self, settings: Settings):
        self.settings = settings

    # --- 延迟生成 ---

    def random_delay(self, mean: float = 1.0, std: float = 0.3) -> float:
        """生成符合对数正态分布的随机延迟（秒）"""
        return max(0.1, random.lognormvariate(math.log(mean), std))

    def uniform_delay(self, min_s: float, max_s: float) -> float:
        """生成均匀分布的随机延迟（秒）"""
        return random.uniform(min_s, max_s)

    # --- 打字模拟 ---

    async def human_type(self, page, selector: str, text: str):
        """模拟人类逐字输入，带随机打字节奏"""
        from playwright.async_api import Page

        await page.click(selector)
        await asyncio.sleep(self.random_delay(0.3, 0.1))

        # 清除已有内容（Ctrl+A + Delete）
        await page.keyboard.press("Control+a")
        await asyncio.sleep(self.random_delay(0.2, 0.05))
        await page.keyboard.press("Delete")
        await asyncio.sleep(self.random_delay(0.3, 0.1))

        for i, char in enumerate(text):
            delay_ms = random.randint(
                self.settings.human_typing_min_ms,
                self.settings.human_typing_max_ms,
            )
            await page.keyboard.type(char, delay=delay_ms)

            # 偶尔模拟"思考"暂停（每50-70个字符一次）
            if i > 0 and i % random.randint(50, 70) == 0:
                await asyncio.sleep(self.uniform_delay(0.3, 1.5))

            # 2%概率模拟打错后纠正
            if random.random() < self.settings._raw.get("human", {}).get("typo_probability", 0.02):
                await page.keyboard.press("Backspace")
                await asyncio.sleep(self.random_delay(0.2, 0.1))
                await page.keyboard.type(char, delay=random.randint(50, 200))

        await asyncio.sleep(self.random_delay(0.5, 0.2))

    # --- 鼠标移动模拟 ---

    async def human_move_mouse(self, page, target_x: int, target_y: int):
        """模拟人类曲线移动鼠标到目标位置"""
        from playwright.async_api import Page

        viewport = page.viewport_size
        start_x = random.randint(50, viewport["width"] - 50)
        start_y = random.randint(50, viewport["height"] - 50)

        steps = self.settings._raw.get("human", {}).get("mouse_move_steps", 10)

        for i in range(1, steps + 1):
            t = i / steps
            # 使用贝塞尔曲线产生自然的非线性移动
            # 简化版：带随机偏移的线性插值
            x = start_x + (target_x - start_x) * t + random.randint(-10, 10)
            y = start_y + (target_y - start_y) * t + random.randint(-10, 10)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.015))

    async def human_click(self, page, selector: Optional[str] = None,
                          x: Optional[int] = None, y: Optional[int] = None):
        """模拟人类点击（先移动再点击）"""
        if selector:
            el = await page.query_selector(selector)
            if el:
                box = await el.bounding_box()
                if box:
                    target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
                    target_y = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
                    await self.human_move_mouse(page, target_x, target_y)
                    await asyncio.sleep(self.random_delay(0.2, 0.1))
                    await page.mouse.click(target_x, target_y)
                    return
        if x is not None and y is not None:
            await self.human_move_mouse(page, x, y)
            await asyncio.sleep(self.random_delay(0.2, 0.1))
            await page.mouse.click(x, y)

    # --- 滚动模拟 ---

    async def human_scroll(self, page, direction: str = "down",
                           pixels: Optional[int] = None):
        """模拟人类逐步滚动页面"""
        if pixels is None:
            pixels = random.randint(300, 800)

        step = pixels // random.randint(3, 5)
        for _ in range(random.randint(3, 5)):
            delta = 0 if direction == "down" else -step
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(self.uniform_delay(0.1, 0.4))

        # 偶尔"阅读"一下（短暂停顿）
        if random.random() < 0.3:
            await asyncio.sleep(self.uniform_delay(1.0, 3.0))

    # --- 检索间隔 ---

    async def inter_search_delay(self, query_index: int):
        """每次检索之间的等待间隔，模拟人类阅读结果"""
        min_s, max_s = self.settings.human_search_interval
        delay = self.uniform_delay(min_s, max_s)

        # 每N次检索后，增加一个长暂停（模拟思考/分析）
        long_interval = self.settings.human_long_pause_interval
        if query_index % long_interval == 0:
            long_min, long_max = self.settings.human_long_pause_range
            delay += self.uniform_delay(long_min, long_max)

        await asyncio.sleep(delay)

    # --- 页面"阅读"模拟 ---

    async def simulate_reading(self, page, seconds: float):
        """模拟人类在页面上阅读的行为"""
        import time
        start = time.time()
        while time.time() - start < seconds:
            # 随机滚动一段
            await self.human_scroll(page, random.choice(["down", "down", "up"]),
                                    random.randint(100, 400))
            # 随机暂停
            await asyncio.sleep(self.uniform_delay(0.5, 2.0))
