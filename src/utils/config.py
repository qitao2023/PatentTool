"""
配置加载模块 - 加载 YAML 配置和 .env 环境变量
"""
import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


class Settings:
    """应用配置，提供类型安全的属性访问"""

    def __init__(self, config_dir: str | Path | None = None):
        if config_dir is None:
            import sys
            if getattr(sys, 'frozen', False):
                # PyInstaller 打包：exe 同目录下的 config/
                config_dir = Path(sys.executable).parent / "config"
            else:
                config_dir = Path(__file__).parent.parent.parent / "config"
        self.config_dir = Path(config_dir)

        # 加载 .env
        env_path = self.config_dir / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        # 加载 settings.yaml
        yaml_path = self.config_dir / "settings.yaml"
        if not yaml_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {yaml_path}")
        with open(yaml_path, "r", encoding="utf-8") as f:
            self._raw: Dict[str, Any] = yaml.safe_load(f)

    @property
    def anthropic_api_key(self) -> str:
        return os.getenv("ANTHROPIC_API_KEY", "")

    @property
    def anthropic_base_url(self) -> str | None:
        return os.getenv("ANTHROPIC_BASE_URL")

    @property
    def deepseek_api_key(self) -> str:
        return os.getenv("DEEPSEEK_API_KEY", "")

    @property
    def deepseek_base_url(self) -> str:
        return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    @property
    def ai_provider(self) -> str:
        """当前AI引擎: deepseek 或 kimi"""
        return self._raw.get("ai", {}).get("provider", "deepseek")

    @property
    def ai_temperature(self) -> float:
        return self._raw.get("ai", {}).get("temperature", 0.4)

    @property
    def ai_model(self) -> str:
        """默认模型"""
        ai = self._raw.get("ai", {})
        if self.ai_provider == "kimi":
            return ai.get("kimi_model", "kimi-k3")
        return ai.get("deepseek_model", "deepseek-v4-flash")

    @property
    def ai_query_provider(self) -> str | None:
        """检索式生成专用提供商（不设则用全局 provider）"""
        return self._raw.get("ai", {}).get("query_provider")

    @property
    def ai_query_model(self) -> str:
        """检索式生成专用模型（默认用旗舰模型）"""
        ai = self._raw.get("ai", {})
        # 如果 query_provider 不同，用对应提供商的默认模型
        qp = self.ai_query_provider
        if qp == "kimi":
            return ai.get("query_model", ai.get("kimi_model", "kimi-k3"))
        if qp == "deepseek":
            return ai.get("query_model", ai.get("deepseek_model", "deepseek-v4-pro"))
        return ai.get("query_model", self.ai_model)

    @property
    def ai_screen_model(self) -> str:
        """筛选/评分专用模型（默认用快速模型）"""
        ai = self._raw.get("ai", {})
        return ai.get("screen_model", self.ai_model)

    @property
    def ai_analysis_model(self) -> str:
        """对比分析专用模型"""
        ai = self._raw.get("ai", {})
        return ai.get("analysis_model", self.ai_model)

    # --- PDF ---
    @property
    def pdf_max_pages(self) -> int:
        return self._raw.get("pdf", {}).get("max_pages", 500)

    @property
    def pdf_ocr_enabled(self) -> bool:
        return self._raw.get("pdf", {}).get("ocr_enabled", False)

    # --- 检索式生成 ---
    @property
    def query_max_queries(self) -> int:
        return self._raw.get("query_generation", {}).get("max_queries", 10)

    @property
    def query_model(self) -> str:
        return self.ai_model

    @property
    def query_max_tokens(self) -> int:
        return self._raw.get("query_generation", {}).get("max_tokens", 8192)

    @property
    def query_max_description_chars(self) -> int:
        """说明书截断长度（保留发明内容 + 实施方式前段），默认 5000"""
        return self._raw.get("query_generation", {}).get("max_description_chars", 5000)

    # --- 提示词模板 ---
    @property
    def prompts_dir(self) -> Path:
        """提示词模板目录"""
        return self.config_dir / "prompts"

    @property
    def prompts_active_profile(self) -> str:
        """当前使用的提示词方案名称"""
        return self._raw.get("query_generation", {}).get("prompt_profile", "default")

    def get_prompt_text(self, profile: str, prompt_type: str) -> str:
        """读取指定 profile 的 prompt 文本（system 或 user）

        Args:
            profile: 方案名称，如 "default" 或 "semiconductor"
            prompt_type: "system" 或 "user"
        Returns:
            prompt 文本；文件不存在返回空字符串
        """
        path = self.prompts_dir / profile / f"{prompt_type}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # --- Web ---
    @property
    def web_browser(self) -> str:
        """浏览器: chrome 或 msedge"""
        return self._raw.get("web", {}).get("browser", "chrome")

    @property
    def web_headless(self) -> bool:
        return self._raw.get("web", {}).get("headless", False)

    @property
    def web_viewport(self) -> tuple[int, int]:
        w = self._raw.get("web", {}).get("viewport_width", 1920)
        h = self._raw.get("web", {}).get("viewport_height", 1080)
        return (w, h)

    @property
    def web_locale(self) -> str:
        return self._raw.get("web", {}).get("locale", "zh-CN")

    @property
    def web_timezone(self) -> str:
        return self._raw.get("web", {}).get("timezone", "Asia/Shanghai")

    @property
    def web_proxy(self) -> str | None:
        return self._raw.get("web", {}).get("proxy")

    @property
    def web_use_cdp(self) -> bool:
        """是否连接到用户已打开的浏览器（CDP 模式）"""
        return self._raw.get("web", {}).get("use_cdp", False)

    @property
    def web_cdp_port(self) -> int:
        return self._raw.get("web", {}).get("cdp_port", 9222)

    @property
    def web_clash_api(self) -> str | None:
        """Clash API 地址，用于切换代理节点"""
        return self._raw.get("web", {}).get("clash_api")

    @property
    def web_proxy_rotate(self) -> list[str]:
        """备用代理列表"""
        return self._raw.get("web", {}).get("proxy_rotate", [])

    # --- PATENTSCOPE ---
    @property
    def patentscope_base_url(self) -> str:
        return self._raw.get("patentscope", {}).get("base_url", "https://patentscope.wipo.int")

    @property
    def patentscope_search_url(self) -> str:
        return self._raw.get("patentscope", {}).get("search_url",
            "https://patentscope.wipo.int/search/en/search.jsf")

    @property
    def patentscope_advanced_search_url(self) -> str:
        return self._raw.get("patentscope", {}).get("advanced_search_url",
            "https://patentscope.wipo.int/search/en/advancedSearch.jsf")

    @property
    def search_include_citations(self) -> bool:
        return self._raw.get("search", {}).get("include_citations", True)

    @property
    def search_force_refresh(self) -> bool:
        return self._raw.get("search", {}).get("force_refresh", False)

    @property
    def search_prefer_cn_family(self) -> bool:
        """优先使用中国同族专利：下载全文时若遇非CN专利，自动查专利族中的CN专利替换"""
        return self._raw.get("search", {}).get("prefer_cn_family", True)

    @property
    def search_stop_after(self) -> str:
        """流程断点: abstracts | screen | download | score | full"""
        v = self._raw.get("search", {}).get("stop_after", "full")
        if isinstance(v, bool) or str(v) not in ("abstracts", "screen", "download", "score", "full"):
            return "full"
        return str(v)

    @property
    def patentscope_max_results(self) -> int:
        return self._raw.get("patentscope", {}).get("max_results_per_query", 100)

    @property
    def patentscope_results_per_page(self) -> int:
        return self._raw.get("patentscope", {}).get("results_per_page", 200)

    @property
    def patentscope_collections(self) -> list[str]:
        return self._raw.get("patentscope", {}).get("collections", ["PCT"])

    @property
    def patentscope_selectors(self) -> dict:
        return self._raw.get("patentscope", {}).get("selectors", {})

    @property
    def patentscope_rate_limit(self) -> int:
        return self._raw.get("patentscope", {}).get("rate_limit_calls_per_hour", 1000)

    # --- 引擎选择 ---
    @property
    def search_source(self) -> str:
        """全链路引擎: wipo | google

        wipo   → 搜索+下载全走 PATENTSCOPE 浏览器（原行为）
        google → 搜索+下载全走 Google Patents（免浏览器）
        两套独立体系，不混用。
        """
        v = str(self._raw.get("search", {}).get("search_source", "google"))
        if v not in ("wipo", "google"):
            return "google"
        return v

    @property
    def google_patents_timeout(self) -> int:
        return int(self._raw.get("google_patents", {}).get("timeout", 20))

    @property
    def search_download_concurrency(self) -> int:
        """下载并发数（主流程下载 + 批量测试共用），默认 20。

        兼容旧配置：优先 search.download_concurrency，
        否则回退 test.batch_default_concurrency。
        """
        v = self._raw.get("search", {}).get(
            "download_concurrency",
            self._raw.get("test", {}).get("batch_default_concurrency", 20))
        return max(1, min(int(v), 50))

    # --- 人类行为 ---
    @property
    def human_typing_min_ms(self) -> int:
        return self._raw.get("human", {}).get("typing_delay_min_ms", 30)

    @property
    def human_typing_max_ms(self) -> int:
        return self._raw.get("human", {}).get("typing_delay_max_ms", 150)

    @property
    def human_search_interval(self) -> tuple[int, int]:
        cfg = self._raw.get("human", {})
        return (cfg.get("search_interval_min_s", 8), cfg.get("search_interval_max_s", 15))

    @property
    def human_long_pause_interval(self) -> int:
        return self._raw.get("human", {}).get("long_pause_interval_queries", 3)

    @property
    def human_long_pause_range(self) -> tuple[int, int]:
        cfg = self._raw.get("human", {})
        return (cfg.get("long_pause_min_s", 25), cfg.get("long_pause_max_s", 40))

    # --- 去重 ---
    @property
    def dedup_threshold(self) -> float:
        return self._raw.get("dedup", {}).get("title_similarity_threshold", 0.90)

    # --- Analysis ---
    @property
    def analysis_model(self) -> str:
        return self._raw.get("analysis", {}).get("model", "claude-sonnet-4-20250514")

    @property
    def analysis_top_n(self) -> int:
        return self._raw.get("analysis", {}).get("top_n_for_detailed", 15)

    @property
    def analysis_max_detail_fetch(self) -> int:
        """全文下载上限，默认 200"""
        return self._raw.get("analysis", {}).get("max_detail_fetch", 1000)

    @property
    def analysis_fulltext_batch_size(self) -> int:
        """全文筛选时每批发给 AI 的篇数，默认 30（兼容旧流程）"""
        return self._raw.get("analysis", {}).get("fulltext_batch_size", 30)

    # --- 全量 Claims 广筛 ---
    @property
    def analysis_screen_content(self) -> str:
        """广筛内容模式:
          claims            只发权利要求（紧凑）
          embodiments       只发具体实施方式（审查员推荐，对比实际实施方案）
          claims+embodiments 两者都发（每篇各占一半预算）
        """
        v = str(self._raw.get("analysis", {}).get("screen_content", "embodiments"))
        if v not in ("claims", "embodiments", "claims+embodiments"):
            return "embodiments"
        return v

    @property
    def analysis_screen_claims_limit(self) -> int:
        """每篇权利要求截断字符，默认 3000"""
        return int(self._raw.get("analysis", {}).get("screen_claims_limit", 3000))

    @property
    def analysis_screen_batch_chars(self) -> int:
        """每批内容字符预算，默认 300000"""
        return int(self._raw.get("analysis", {}).get("screen_batch_chars", 300000))

    @property
    def analysis_screen_concurrency(self) -> int:
        """广筛批并发数，默认 3"""
        return int(self._raw.get("analysis", {}).get("screen_max_concurrency", 3))

    # --- 终选评述 ---
    @property
    def analysis_final_pool_top_n(self) -> int:
        """终选候选池 = 历史最佳前 N，默认 50"""
        return int(self._raw.get("analysis", {}).get("final_pool_top_n", 50))

    @property
    def analysis_final_pool_min_score(self) -> int:
        """低于此分不进终选候选池，默认 55"""
        return int(self._raw.get("analysis", {}).get("final_pool_min_score", 55))

    @property
    def analysis_final_review_n(self) -> int:
        """终选评述篇数，默认 8"""
        return int(self._raw.get("analysis", {}).get("final_review_n", 8))

    # --- Session ---
    @property
    def session_max_age_hours(self) -> int:
        return self._raw.get("session", {}).get("max_age_hours", 6)

    @property
    def session_profile_dir(self) -> str:
        return self._raw.get("session", {}).get("profile_dir", "profiles/patentscope_browser")

    # --- 测试工具默认值 ---
    @property
    def test_default_query(self) -> str:
        """测试工具 - 默认检索式"""
        return self._raw.get("test", {}).get("default_query", "掉电")

    @property
    def test_default_count(self) -> int:
        """测试工具 - 默认数量上限"""
        return self._raw.get("test", {}).get("default_count", 10)

    @property
    def test_batch_default_count(self) -> int:
        """批量测试 - 默认每式结果上限"""
        return self._raw.get("test", {}).get("batch_default_count", 100)

    @property
    def test_batch_default_concurrency(self) -> int:
        """批量测试 - 默认下载并发数"""
        return self._raw.get("test", {}).get("batch_default_concurrency", 1)

    @property
    def test_batch_default_queries(self) -> list[str]:
        """批量测试 - 默认检索式列表"""
        return self._raw.get("test", {}).get("batch_default_queries", [])

    def save_test_defaults(self, batch_queries: list[str]):
        """保存批量测试的默认检索式列表到 settings.yaml。

        每式结果数/并发已改由主面板和 ⚙设置 统一管理（不在此保存）。
        """
        yaml_path = self.config_dir / "settings.yaml"
        if not yaml_path.exists():
            return
        import re
        import yaml as _yaml
        content = yaml_path.read_text(encoding="utf-8")
        # 批量检索式列表：使用 yaml.dump 生成去掉缩进后的纯列表行
        queries_yaml = _yaml.dump(
            batch_queries, default_flow_style=True,
            allow_unicode=True, sort_keys=False,
            width=10**6,  # 禁止自动换行
        ).strip()
        if re.search(r'batch_default_queries:\s*\[.*\]', content):
            content = re.sub(
                r'batch_default_queries:\s*\[.*\]',
                f'batch_default_queries: {queries_yaml}',
                content)
        else:
            content = re.sub(
                r'(download_concurrency:\s*\d+)',
                f'\\1\n  batch_default_queries: {queries_yaml}',
                content)
        yaml_path.write_text(content, encoding="utf-8")
        # 刷新内存缓存
        with open(yaml_path, "r", encoding="utf-8") as _f:
            self._raw = _yaml.safe_load(_f)

    @staticmethod
    def _yaml_set_str(content: str, key: str, value: str, section: str) -> str:
        import re
        # 使用 (?m)^ 锚定行首，防止 default_query 误匹配 batch_default_queries 等
        pattern = rf'(?m)^(\s*{key}:\s*)"[^"]*"'
        if re.search(pattern, content):
            return re.sub(pattern, rf'\g<1>"{value}"', content)
        else:
            # 在 section 下追加
            return re.sub(
                rf'({section}:\s*\n)',
                rf'\g<1>  {key}: "{value}"\n',
                content)

    @staticmethod
    def _yaml_set_int(content: str, key: str, value: int, section: str) -> str:
        import re
        # 使用 (?m)^ 锚定行首，防止 default_count 误匹配 batch_default_count 等
        pattern = rf'(?m)^(\s*{key}:\s*)\d+'
        if re.search(pattern, content):
            return re.sub(pattern, rf'\g<1>{value}', content)
        else:
            return re.sub(
                rf'({section}:\s*\n)',
                rf'\g<1>  {key}: {value}\n',
                content)

    # --- 输出 ---
    @property
    def output_report_formats(self) -> list[str]:
        return self._raw.get("output", {}).get("report_formats", ["json", "html"])

    def get(self, *keys: str, default: Any = None) -> Any:
        """深层字典取值"""
        d = self._raw
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k)
            else:
                return default
        return d if d is not None else default
