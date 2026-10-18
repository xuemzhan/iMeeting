"""OpenAI 兼容 LLM 客户端。

支持任意 OpenAI 兼容端点（云端 API 或本地 vLLM）。
只依赖 httpx，不引入额外 SDK。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from ..settings import get_config

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败。"""


class LLMClient:
    """轻量 OpenAI 兼容客户端。"""

    def __init__(self) -> None:
        base = str(get_config("llm.base_url") or "").rstrip("/")
        key = str(get_config("llm.api_key") or "")
        self.base_url = base
        self.api_key = key
        self.model = str(get_config("llm.model") or "")
        self.timeout = float(get_config("llm.timeout") or 120)
        self.retries = int(get_config("llm.retries") or 2)

    @property
    def configured(self) -> bool:
        """是否具备调用条件。

        除端点与模型名外，**公网端点还必须有 API Key**。
        否则未配置的部署会拿着一堆默认值去真实发起 HTTPS 请求，
        再叠加 ``llm.retries`` 次退避重试，用户点「生成纪要」后要干等几十秒
        才看到「未配置」——不如一开始就明确拒绝。

        本地端点（如 vLLM 的 http://localhost:8000/v1）通常不需要 Key，
        因此不计入这一限制。
        """
        if not (self.base_url and self.model):
            return False
        return bool(self.api_key) or self._is_local_endpoint()

    def _is_local_endpoint(self) -> bool:
        from urllib.parse import urlparse

        try:
            host = (urlparse(self.base_url).hostname or "").lower()
        except ValueError:
            return False
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local")

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        model: str | None = None,
        meeting_id: int | None = None,
        purpose: str = "",
    ) -> str:
        """发起一次对话补全，返回文本内容。"""
        if not self.configured:
            raise LLMError("LLM 未配置，请在后台填写 API 端点与模型名")

        use_model = (model or self.model).strip()
        payload: dict[str, Any] = {
            "model": use_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else float(get_config("llm.temperature") or 0.3),
            "max_tokens": max_tokens or int(get_config("llm.max_tokens") or 4096),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None

        from . import guard

        try:
            await guard.acquire(meeting_id)
        except RuntimeError as exc:
            raise LLMError(str(exc)) from exc
        try:
            for attempt in range(self.retries + 1):
                try:
                    t0 = time.perf_counter()
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        resp = await client.post(
                            f"{self.base_url}/chat/completions",
                            json=payload,
                            headers=headers,
                        )

                        if resp.status_code == 401:
                            raise LLMError("API Key 无效或已过期")
                        if resp.status_code == 404:
                            raise LLMError(
                                f"端点或模型不存在：{self.base_url} / {self.model}"
                            )
                        if resp.status_code == 429:
                            retry_after = resp.headers.get("Retry-After", "")
                            raise LLMError(
                                "LLM 供应商限流"
                                + (f"，请 {retry_after} 秒后重试" if retry_after else "")
                            )
                        if resp.status_code >= 400:
                            detail = resp.text[:200]
                            raise LLMError(f"LLM 返回 {resp.status_code}：{detail}")

                        data = resp.json()
                        content = data["choices"][0]["message"]["content"] or ""
                        usage = data.get("usage") or {}
                        prompt_chars = sum(
                            len(m.get("content") or "") for m in messages
                        )
                        prompt_tokens = int(
                            usage.get("prompt_tokens")
                            or max(1, prompt_chars // 2)
                        )
                        completion_tokens = int(
                            usage.get("completion_tokens")
                            or max(1, len(content) // 2)
                        )
                        cost = guard.estimate_cost(
                            prompt_tokens, completion_tokens
                        )
                        logger.info(
                            "llm_call model=%s attempt=%d prompt_tokens=%d "
                            "completion_tokens=%d cost_usd=%.6f elapsed_ms=%.0f",
                            use_model,
                            attempt + 1,
                            prompt_tokens,
                            completion_tokens,
                            cost,
                            (time.perf_counter() - t0) * 1000,
                        )
                        self._record_usage(
                            meeting_id=meeting_id,
                            purpose=purpose,
                            model=use_model,
                            prompt_chars=prompt_chars,
                            response_chars=len(content),
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            cost_usd=cost,
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        return content

                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_error = exc
                    if attempt < self.retries:
                        wait = 1.5 * (attempt + 1)
                        logger.warning(
                            "LLM 请求失败，%.1fs 后重试 (%d/%d)",
                            wait,
                            attempt + 1,
                            self.retries,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise LLMError(f"LLM 连接失败：{exc}") from exc
                except LLMError:
                    raise
                except Exception as exc:
                    last_error = exc
                    break
        finally:
            guard.release()

        raise LLMError(f"LLM 调用失败：{last_error}")

    @staticmethod
    def _record_usage(
        *,
        meeting_id: int | None,
        purpose: str,
        model: str,
        prompt_chars: int,
        response_chars: int,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        elapsed_ms: int,
    ) -> None:
        try:
            from ..db import session_scope
            from ..models import LLMUsage

            with session_scope() as db:
                db.add(
                    LLMUsage(
                        meeting_id=meeting_id,
                        purpose=purpose[:64],
                        model=model[:128],
                        prompt_chars=prompt_chars,
                        response_chars=response_chars,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost_usd=cost_usd,
                        elapsed_ms=elapsed_ms,
                    )
                )
        except Exception:
            logger.exception("LLM 用量记录失败")


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中稳健地抽出 JSON。

    模型可能把 JSON 包在 ```json 代码块里，或前后带解释文字，
    因此先尝试直接解析，失败再用正则定位最外层大括号。
    """
    text = text.strip()

    # 去掉 markdown 代码块围栏
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 定位第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            pass

    return None
