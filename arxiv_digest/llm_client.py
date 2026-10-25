"""统一的模型调用入口（Chat Completions + JSON 输出）。

单独一层的原因，都是实打实的端点差异：

1. **`response_format` 不是处处支持**。OpenAI、DeepSeek 支持；一些兼容网关会报
   `Unrecognized request argument`。这里在失败时自动降级为"提示词约束 JSON"重试。
2. **DeepSeek 的 JSON Output 有概率返回空 content**（文档自己写了"正在优化"）。
   空返回必须重试，不能让整封邮件降级成英文。
3. **思考模式是 DeepSeek 特有参数，而且默认开启**。开启时 `temperature` 等参数无效，
   且思考 token 按输出计费。这个参数**不能发给不认识的端点**（OpenAI 会直接 400），
   所以用三态：`None` = 不发，`True/False` = 显式开关。
4. **`max_tokens` 必须显式给**。DeepSeek 文档明确要求：不设上限，JSON 可能被中途截断。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _parse_json_object(text: str) -> dict[str, Any]:
    """把模型输出解析成 dict；容忍 ```json 围栏与前后废话。"""

    cleaned = _CODE_FENCE_RE.sub("", text.strip())
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(cleaned[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    raise ValueError("模型返回的内容不是可解析的 JSON 对象")


@dataclass(frozen=True)
class LLMOptions:
    """一次调用的形状。"""

    temperature: float = 0.3
    max_tokens: int = 4000
    thinking: bool | None = None  # None = 不发这个参数
    attempts: int = 2


DEFAULT_OPTIONS = LLMOptions()

# 截断时最多放大到这个数（DeepSeek 上限 384K，但一天四封信不需要那么多）。
MAX_TOKENS_CEILING = 32_000


def _format_rejected(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json_object" in text or "json mode" in text


def call_json(
    client: Any,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    options: LLMOptions | None = None,
) -> dict[str, Any]:
    """发一次请求并要求 JSON 对象；返回解析后的 dict。

    失败会抛异常，由调用方决定"降级"还是"重试"——这一层不吞异常，否则排查时看不到原因。
    """

    opts = options or DEFAULT_OPTIONS
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    use_format = True
    limit = opts.max_tokens
    thinking = opts.thinking
    if thinking is None and "deepseek" in model.lower():
        # DeepSeek 默认开思考。结构化抽取/翻译/打分不需要，反而会吃掉 max_tokens 预算。
        thinking = False
    last_error: Exception | None = None
    for attempt in range(1, max(1, opts.attempts) + 1):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": limit,
        }
        if thinking is not True:
            kwargs["temperature"] = opts.temperature
        if thinking is not None:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled" if thinking else "disabled"}
            }
        if use_format:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - 需要按错误内容决定是否降级
            last_error = exc
            if use_format and _format_rejected(exc):
                LOGGER.warning("该端点不接受 response_format，降级为提示词约束 JSON：%s", exc)
                use_format = False
                continue
            raise

        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        _log_usage(model, getattr(response, "usage", None))

        content = (choice.message.content or "").strip() if choice else ""
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        if finish_reason == "length":
            if thinking is True and not content:
                # 思考把 max_tokens 全吃在 reasoning 上，content 是空的。
                # 继续放大只会更慢更贵，直接关掉思考重试。
                LOGGER.warning("思考模式吃光了 max_tokens 预算（%s），关掉思考重试。", model)
                thinking = False
                continue

            raised = min(limit * 2, MAX_TOKENS_CEILING)
            if raised > limit:
                LOGGER.warning(
                    "输出被 max_tokens=%d 截断（%s），放大到 %d 重试。",
                    limit,
                    model,
                    raised,
                )
                limit = raised
                continue

        if not content:
            LOGGER.warning(
                "第 %d/%d 次返回空 content（部分端点的 JSON 模式已知问题），重试。",
                attempt,
                max(1, opts.attempts),
            )
            continue
        return _parse_json_object(content)

    if last_error is not None:
        raise last_error
    raise ValueError(
        f"模型 {max(1, opts.attempts)} 次尝试后仍没有返回内容（最后一次 finish_reason={finish_reason!r}）。"
    )


def _log_usage(model: str, usage: Any) -> None:
    """把真实用量打出来——调 max_tokens 时依据的是这个数字，不是猜。"""

    if usage is None:
        return
    completion = getattr(usage, "completion_tokens", None)
    if completion is None:
        return
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    prompt = getattr(usage, "prompt_tokens", None)
    LOGGER.info(
        "模型用量 %s：输入 %s / 输出 %s%s",
        model,
        prompt if prompt is not None else "?",
        completion,
        f"（其中思考 {reasoning}）" if reasoning else "",
    )


def text_options(temperature: float = 0.3, *, max_tokens: int = 2000, thinking: bool | None = None) -> LLMOptions:
    """给"只要一段文本、不要太长"的调用用的预设。"""

    return LLMOptions(temperature=temperature, max_tokens=max_tokens, thinking=thinking)
