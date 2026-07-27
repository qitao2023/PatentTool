"""
统一 AI 客户端 - 支持 DeepSeek / Kimi 两种后端切换（均为 OpenAI 兼容接口）

DeepSeek 最新模型 (2026-07):
  - deepseek-v4-flash: 通⽤模型, 1M上下⽂, 384K最⼤输出
  - deepseek-v4-pro: 旗舰模型, 1.6T参数/49B激活, 1M上下⽂
  (deepseek-chat/reasoner 已于 2026-07-24 下线)

Kimi 最新模型 (2026-07):
  - kimi-k2.6: 旗舰模型, 262K上下⽂, ⽀持⽂本/图⽚/视频, 混合思考
  - kimi-k2.7-code: 编程专⽤, 262K上下⽂, 始终思考模式
"""
from openai import OpenAI


# 提供商配置表
PROVIDER_CONFIG = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "models": {
            "deepseek-v4-flash": "DeepSeek V4 Flash（通用，1M上下文）",
            "deepseek-v4-pro": "DeepSeek V4 Pro（旗舰，1.6T参数）",
        },
    },
    "kimi": {
        "api_key_env": "KIMI_API_KEY",
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "kimi-k3",
        "models": {
            "kimi-k3": "Kimi K3（最新旗舰，2.8T参数，1M上下文）",
            "kimi-k2.6": "Kimi K2.6（262K上下文，图文视频）",
            "kimi-k2.5": "Kimi K2.5（全能，262K）",
            "kimi-k2.7-code": "Kimi K2.7 Code（编程专用，262K）",
            "moonshot-v1-128k": "Moonshot V1 128K（旧版）",
        },
    },
}


def get_available_providers() -> list[str]:
    """返回支持的提供商列表"""
    return list(PROVIDER_CONFIG.keys())


def get_provider_models(provider: str) -> dict[str, str]:
    """返回某个提供商支持的模型列表 {model_name: display_name}"""
    cfg = PROVIDER_CONFIG.get(provider, {})
    return cfg.get("models", {})


def get_default_model(provider: str) -> str:
    """返回某个提供商的默认模型"""
    cfg = PROVIDER_CONFIG.get(provider, {})
    return cfg.get("default_model", "")


class AIClient:
    """统一 AI 客户端，适配 DeepSeek / Kimi"""

    def __init__(self, settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider or settings.ai_provider
        self._client: OpenAI | None = None
        self._log_dir = None  # 可选，用于保存每次 AI 交互内容
        self._call_counter = 0

    def set_log_dir(self, path):
        """设置 AI 交互日志目录，设置后每次 chat 都会保存 prompt 和 response"""
        from pathlib import Path
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        self._log_dir = p
        self._call_counter = 0

    @property
    def provider(self) -> str:
        return self._provider

    @provider.setter
    def provider(self, value: str):
        if value != self._provider:
            self._provider = value
            self._client = None

    def _get_api_key(self) -> str:
        import os
        provider_cfg = PROVIDER_CONFIG.get(self._provider, {})
        env_var = provider_cfg.get("api_key_env", "")
        return os.getenv(env_var, "") if env_var else ""

    def _get_base_url(self) -> str:
        provider_cfg = PROVIDER_CONFIG.get(self._provider, {})
        default_url = provider_cfg.get("base_url", "")
        import os
        return os.getenv(f"{self._provider.upper()}_BASE_URL", default_url)

    def _get_model(self) -> str:
        return self.settings.ai_model

    def _ensure_client(self):
        if self._client is not None:
            return
        api_key = self._get_api_key()
        if not api_key:
            cfg = PROVIDER_CONFIG.get(self._provider, {})
            raise ValueError(
                f"未配置 {self._provider} API Key。\n"
                f"请在 config/.env 中设置 {cfg['api_key_env']}=sk-xxx"
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=self._get_base_url(),
        )

    def chat(self, system_prompt: str, user_prompt: str,
             max_tokens: int = 4096, temperature: float | None = None,
             json_mode: bool = False) -> str:
        """统一的聊天接口。json_mode=True 时强制输出合法 JSON。"""
        self._ensure_client()
        if temperature is None:
            temperature = self.settings.ai_temperature

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        kwargs = dict(
            model=self._get_model(),
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""

        # 保存 AI 交互日志
        if self._log_dir:
            self._call_counter += 1
            import json as _json
            from datetime import datetime
            ts = datetime.now().strftime("%H%M%S")
            log_file = self._log_dir / f"{self._call_counter:03d}_{ts}.json"
            log_file.write_text(_json.dumps({
                "call": self._call_counter,
                "model": self._get_model(),
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response": content,
            }, indent=2, ensure_ascii=False), encoding="utf-8")

        return content
