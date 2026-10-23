import json
from typing import Any


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型文本中提取单个 JSON object，供结构化输出校验使用。

    Args: text 为模型响应文本，可包含 Markdown fence 或额外说明。
    Returns: 解析后的字典对象。
    Raises: 找不到 object、JSON 无效或顶层不是 object 时抛出 ValueError。
    副作用：无；由 analyzer、verifier 和 report generator 调用。
    """
    normalized = _strip_markdown_fence(text)
    decoder = json.JSONDecoder()

    for index, char in enumerate(normalized):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(normalized[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("LLM response does not contain a JSON object")
