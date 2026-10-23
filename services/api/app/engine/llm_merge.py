from copy import deepcopy
from typing import Any


MERGE_KEYS = ["evidence", "findings", "risks", "questions", "recommendations"]


def merge_llm_analysis(analysis: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
    """将通过校验的 LLM 分析追加到确定性分析，保留原有证据引用。

    Args: analysis 为确定性分析；llm_payload 为已通过 validator 的 LLM 内容。
    Returns: 合并后的分析字典。
    Raises: 输入集合不是预期结构时可能抛出 KeyError 或 TypeError。
    副作用：不访问外部系统；返回新字典，不直接写数据库。
    """
    # 深拷贝确定性分析，避免把 LLM 补充结果直接写回调用者持有的对象。
    merged = deepcopy(analysis)

    for key in MERGE_KEYS:
        # 每个 key 对应一种 Analysis Package 集合，缺失时统一按空列表处理。
        merged.setdefault(key, [])
        items = llm_payload.get(key, [])
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                raise ValueError("LLM analysis item must be an object")

            # 为每条 LLM 项复制 metadata，并标记来源，便于后续审计和报告展示。
            appended = deepcopy(item)
            metadata = appended.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["source"] = "llm_analyzer"
            appended["metadata"] = metadata
            merged[key].append(appended)

    # 返回新的合并字典；该函数本身不负责数据库持久化。
    return merged
