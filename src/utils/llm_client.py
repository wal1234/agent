"""LLM 调用封装层

支持的 Provider：
- volcengine / ark  火山引擎方舟（OpenAI 兼容协议）⭐ 默认
- openai            OpenAI / 任何 OpenAI 兼容网关
- anthropic         Anthropic Claude

火山引擎方舟接入说明：
- API 地址：https://ark.cn-beijing.volces.com/api/v3
- 鉴权方式：Bearer <ARK_API_KEY>
- 模型字段：填模型名（如 glm-4-32k / doubao-pro-32k）或 endpoint ID（如 ep-xxxxxx）
- 文档：https://www.volcengine.com/docs/82379/1099455
"""

import os
from typing import Dict, Any, Optional


# 火山引擎方舟默认配置
ARK_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
ARK_DEFAULT_MODEL = "glm-5.1"   # 默认模型 ID（可在 config.yaml 中覆盖为 endpoint ID）

# 环境变量优先级
PROVIDER_ENV_KEYS = {
    "volcengine": ["ARK_API_KEY", "VOLC_ARK_API_KEY", "VOLCENGINE_API_KEY"],
    "ark":        ["ARK_API_KEY", "VOLC_ARK_API_KEY", "VOLCENGINE_API_KEY"],
    "openai":     ["OPENAI_API_KEY"],
    "anthropic":  ["ANTHROPIC_API_KEY"],
}


class LLMNotConfiguredError(RuntimeError):
    """LLM 未配置 API Key 时抛出 - 分析器应捕获此异常走 fallback 路径"""
    pass


class LLMClient:
    """LLM 客户端（按需懒加载 SDK）

    火山引擎方舟示例：
        client = LLMClient(
            provider="volcengine",
            model="glm-5.1",  # 或 endpoint ID 如 "ep-20251201-xxxx"
            api_key=os.environ["ARK_API_KEY"],
        )
        # 内部：实际使用 OpenAI SDK + base_url = ARK_DEFAULT_BASE_URL
    """

    def __init__(
        self,
        provider: str = "volcengine",
        model: str = ARK_DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ):
        self.provider = provider.lower()
        self.model = model
        self.api_key = api_key or self._resolve_api_key(self.provider)
        self.base_url = base_url or self._default_base_url(self.provider)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None

    # ---------- 默认配置 ----------
    @staticmethod
    def _default_base_url(provider: str) -> str:
        if provider in ("volcengine", "ark"):
            return ARK_DEFAULT_BASE_URL
        return ""  # openai / anthropic 走官方默认

    @staticmethod
    def _resolve_api_key(provider: str) -> str:
        for env_key in PROVIDER_ENV_KEYS.get(provider, []):
            value = os.environ.get(env_key)
            if value:
                return value
        return ""

    # ---------- 客户端初始化（懒加载）----------
    def _ensure_client(self):
        if self._client is not None:
            return

        if self.provider in ("volcengine", "ark", "openai"):
            # 火山引擎方舟使用 OpenAI 兼容协议
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        elif self.provider == "anthropic":
            from anthropic import Anthropic
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Anthropic(**kwargs)
        else:
            raise ValueError(f"未支持的 LLM provider: {self.provider}")

    # ---------- 推理调用 ----------
    def complete(self, system: str, user: str) -> str:
        """单轮对话补全

        Args:
            system: 系统指令
            user:   用户消息

        Returns:
            模型回复文本

        Raises:
            LLMNotConfiguredError: 未配置 API Key
            ValueError: provider 不支持
        """
        if not self.api_key:
            raise LLMNotConfiguredError(
                f"LLM 未配置 API Key（provider={self.provider}）。"
                f"请在 config/config.yaml 设置 llm.api_key，"
                f"或设置环境变量 {' / '.join(PROVIDER_ENV_KEYS.get(self.provider, ['<UNKNOWN>']))}。"
            )

        self._ensure_client()

        # 火山引擎/OpenAI：chat.completions
        if self.provider in ("volcengine", "ark", "openai"):
            resp = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content

        # Anthropic
        if self.provider == "anthropic":
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text

        raise ValueError(f"未支持的 provider: {self.provider}")

    # ---------- 配置加载 ----------
    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "LLMClient":
        llm_cfg = cfg.get("llm", {})
        provider = (llm_cfg.get("provider") or "volcengine").lower()

        # api_key 支持 ${ENV_VAR} 占位符
        api_key = llm_cfg.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
            api_key = os.environ.get(api_key[2:-1], "")

        return cls(
            provider=provider,
            model=llm_cfg.get("model", ARK_DEFAULT_MODEL),
            api_key=api_key,
            base_url=llm_cfg.get("base_url", ""),
            max_tokens=llm_cfg.get("max_tokens", 4096),
            temperature=llm_cfg.get("temperature", 0.2),
        )

    # ---------- 调试辅助 ----------
    def describe(self) -> Dict[str, Any]:
        """返回当前 LLM 配置摘要（不暴露 API Key）"""
        return {
            "provider":      self.provider,
            "model":         self.model,
            "base_url":      self.base_url or "<provider default>",
            "api_key":       "configured" if self.api_key else "missing",
            "max_tokens":    self.max_tokens,
            "temperature":   self.temperature,
        }
