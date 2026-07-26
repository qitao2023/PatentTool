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
        """根据 provider 返回对应模型名"""
        ai = self._raw.get("ai", {})
        if self.ai_provider == "kimi":
            return ai.get("kimi_model", "kimi-k3")
        return ai.get("deepseek_model", "deepseek-v4-flash")

    @property
    def himmpat_username(self) -> str | None:
        return os.getenv("HIMMPAT_USERNAME")

    @property
    def himmpat_password(self) -> str | None:
        return os.getenv("HIMMPAT_PASSWORD")

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
        return self._raw.get("query_generation", {}).get("max_tokens", 4096)

    # --- Web ---
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
    def patentscope_max_results(self) -> int:
        return self._raw.get("patentscope", {}).get("max_results_per_query", 200)

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

    # --- HimmPat（保留备用）---
    @property
    def himmpat_base_url(self) -> str:
        return self._raw.get("himmpat", {}).get("base_url", "https://www.himmpat.com")

    @property
    def himmpat_search_url(self) -> str:
        return self._raw.get("himmpat", {}).get("search_url",
                                                 "https://www.himmpat.com/intelligence?active=6")

    @property
    def himmpat_login_url(self) -> str:
        return self._raw.get("himmpat", {}).get("login_url",
                                                 "https://rd.himmpat.com/login")

    @property
    def himmpat_login_mode(self) -> str:
        return self._raw.get("himmpat", {}).get("login_mode", "manual")

    @property
    def himmpat_max_results(self) -> int:
        return self._raw.get("himmpat", {}).get("max_results_per_query", 50)

    @property
    def himmpat_results_per_page(self) -> int:
        return self._raw.get("himmpat", {}).get("results_per_page", 20)

    @property
    def himmpat_selectors(self) -> dict:
        return self._raw.get("himmpat", {}).get("selectors", {})

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

    # --- Session ---
    @property
    def session_max_age_hours(self) -> int:
        return self._raw.get("session", {}).get("max_age_hours", 6)

    @property
    def session_profile_dir(self) -> str:
        return self._raw.get("session", {}).get("profile_dir", "profiles/himmpat_browser")

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
