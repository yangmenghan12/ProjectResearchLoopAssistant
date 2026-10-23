from copy import deepcopy
from dataclasses import dataclass
from typing import Any


REQUIRED_COLLECTION_KEYS = ["evidence", "findings", "risks", "questions", "recommendations"]


@dataclass
class LLMAnalysisValidation:
    accepted: bool  # LLM 输出是否通过结构和证据引用校验。
    content: dict[str, Any]  # 经过规范化、可供合并的 LLM 分析内容。
    validation: dict[str, Any]  # 校验状态、错误消息和诊断信息。


def _normalize_collection(
    payload: dict[str, Any],
    key: str,
    messages: list[str],
    *,
    required: bool = False,
) -> list[Any]:
    """把模型输出中的集合复制为独立列表，供后续校验安全地补全字段。
    验证集合是否存在、类型是否正确，并在必要时记录错误消息。
    由于 LLM 输出可能缺失集合或返回非列表类型，调用方必须使用返回值而不是直接访问原始 payload。


    Args: payload 为原始 JSON 对象；key 为集合字段名；messages 收集错误；required 表示字段必须存在。
    Returns: 字段为 list 时返回深拷贝；缺失或类型错误时返回空列表。
    Raises: payload 不支持成员检查或索引访问时可能抛出类型异常。
    副作用：必要时向 messages 添加错误；由校验入口调用，不修改原始 payload。
    """
    if key not in payload:
        if required:
            messages.append(f"{key} is required")
        return []

    value = payload[key]
    if not isinstance(value, list):
        messages.append(f"{key} must be a list")
        return []
    return deepcopy(value)


def _reasoning_summary(payload: dict[str, Any]) -> str:
    """读取并清理模型可选的推理摘要，非字符串值按空摘要处理。

    Args: payload 为模型返回的 JSON 对象。
    Returns: 去除首尾空白的 reasoning_summary；缺失或类型错误时为 `""`。
    Raises: payload 不支持 get 操作时可能抛出类型异常。
    副作用：无；由分析输出规范化流程调用。
    """
    value = payload.get("reasoning_summary", "")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _message_prefix(key: str, index: int, field: str) -> str:
    """生成带集合名、下标和字段名的统一错误路径。

    Args: key 为集合名；index 为原始条目下标；field 为字段名。
    Returns: 形如 `findings[0].title` 的错误定位字符串。
    Raises: 无显式异常。
    副作用：无；由字段校验函数调用。
    """
    return f"{key}[{index}].{field}"


def _is_non_empty_string(value: Any) -> bool:
    """判断值是否为包含至少一个非空白字符的字符串。

    Args: value 为待检查的任意值。
    Returns: 满足非空字符串条件时为 True，否则为 False。
    Raises: 无显式异常。
    副作用：无；由字段和 client_ref 校验调用。
    """
    return isinstance(value, str) and bool(value.strip())


def _validate_required_string(
    item: dict[str, Any], key: str, index: int, field: str, messages: list[str]
) -> None:
    """检查条目的必填字段是否为非空字符串。

    Args: item 为当前条目；key/index/field 用于定位字段；messages 收集错误。
    Returns: None；缺失或空白字段通过 messages 报告。
    Raises: item 非映射对象时可能抛出访问异常。
    副作用：可能向 messages 追加一条错误；由各集合校验调用。
    """
    if not _is_non_empty_string(item.get(field)):
        messages.append(f"{_message_prefix(key, index, field)} must be a non-empty string")


def _validate_optional_string(
    item: dict[str, Any], key: str, index: int, field: str, messages: list[str]
) -> None:
    """检查可选字段存在时是否为字符串，允许字段缺失。

    Args: item 为当前条目；key/index/field 用于定位字段；messages 收集错误。
    Returns: None；存在且类型错误时追加诊断。
    Raises: item 非映射对象时可能抛出访问异常。
    副作用：可能向 messages 追加一条错误；由问题条目校验调用。
    """
    if field in item and not isinstance(item[field], str):
        messages.append(f"{_message_prefix(key, index, field)} must be a string")


def _validate_optional_confidence(
    item: dict[str, Any], key: str, index: int, messages: list[str]
) -> None:
    """校验可选 confidence 是 0 到 100 的数值，排除 bool 混入分数。

    Args: item 为当前条目；key 为集合名；index 为原始下标；messages 收集错误。
    Returns: None；字段缺失时视为可接受。
    Raises: 本函数不主动抛出业务异常。
    副作用：值无效时追加诊断消息；由 evidence、finding 和 recommendation 校验调用。
    """
    if "confidence" not in item:
        return

    # value 取原始字段值；bool 虽属于 int 的子类，这里仍要拒绝。
    value = item["confidence"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        messages.append(f"{_message_prefix(key, index, 'confidence')} must be between 0 and 100")


def _validate_optional_metadata(
    item: dict[str, Any], key: str, index: int, messages: list[str]
) -> None:
    """补齐缺失的 metadata，并拒绝非对象值，统一后续合并的输入形状。

    Args: item 为当前条目；key 为集合名；index 为原始下标；messages 收集错误。
    Returns: None；缺失 metadata 时在 item 中填入空字典。
    Raises: 本函数不主动抛出业务异常。
    副作用：可能修改规范化副本 item，或追加诊断消息；由各类条目校验调用。
    """
    if "metadata" not in item:
        item["metadata"] = {}
        return
    if not isinstance(item["metadata"], dict):
        messages.append(f"{_message_prefix(key, index, 'metadata')} must be an object")


def _validate_evidence_refs(
    item: dict[str, Any],
    key: str,
    index: int,
    allowed_evidence_refs: set[str],
    messages: list[str],
) -> None:
    """检查 finding/risk 的证据引用是否为非空列表且指向已知证据。

    Args: item 为当前条目；key/index 定位原始条目；allowed_evidence_refs 为可引用 ID；messages 收集错误。
    Returns: None；无效引用通过 messages 报告，不返回过滤后的列表。
    Raises: 本函数不主动抛出业务异常。
    副作用：缺失或非列表时把副本中的 evidence_refs 设为空列表；由 finding/risk 校验调用。
    """
    # refs 是模型给当前条目的引用列表；只有引用 ID 属于 allowed_evidence_refs 才可接受。
    refs = item.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) == 0:
        messages.append(f"{_message_prefix(key, index, 'evidence_refs')} must be a non-empty list")
        item["evidence_refs"] = []
        return

    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            messages.append(
                f"{_message_prefix(key, index, 'evidence_refs')} must be a non-empty list of strings"
            )
            continue
        if ref not in allowed_evidence_refs:
            messages.append(f"Unknown evidence ref: {ref}")


def _validate_items_are_objects(
    content: dict[str, Any], key: str, messages: list[str]
) -> list[tuple[int, dict[str, Any]]]:
    """筛出集合中的对象条目，同时保留原始下标以准确定位错误。
        做前置类型校验，先把格式明显不对的元素过滤掉

    Args: content 为规范化结果；key 为集合名；messages 收集非对象条目的错误。
    Returns: `(原始下标, 条目字典)` 列表；非对象项不进入后续字段校验。
    Raises: content 缺少 key 或对应值不可迭代时抛出异常。
    副作用：可能追加诊断消息；由各类条目校验调用。
    """
    # objects 保留合法对象及其原始位置，避免跳过坏条目后错误下标发生偏移。
    objects: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(content[key]):
        if not isinstance(item, dict):
            messages.append(f"{key}[{index}] must be an object")
            continue
        objects.append((index, item))
    return objects


def _validate_evidence_client_ref(
    item: dict[str, Any],
    index: int,
    allowed_evidence_refs: set[str],
    seen_client_refs: set[str],
    messages: list[str],
) -> str | None:
    """校验新增证据的 client_ref 前缀、已有引用冲突和本批重复情况。
    核心是：禁止 LLM 把自己编造的东西，冒充成系统采集的原始证据。
    先查重复 → 再查基线白名单 → 再查是否为 `llm:` 开头的新增证据 → 都不符合则判非法。

    Args: item 为证据条目；index 为原始下标；allowed_evidence_refs 为已有引用；seen_client_refs 为本批已见值；messages 收集错误。
    Returns: 通过这三项引用规则的 client_ref；缺失或不通过时为 None。
    Raises: 本函数不主动抛出业务异常。
    副作用：非空引用加入 seen_client_refs，可能追加错误；由证据条目校验调用。
    """
    # client_ref 是模型生成的临时证据 ID，持久化时会被映射到数据库 ID。
    client_ref = item.get("client_ref")
    if not _is_non_empty_string(client_ref):
        return None

    # valid 只表示引用自身的三项规则通过，不代表整条 evidence 已通过字段校验。
    valid = True
    if client_ref in allowed_evidence_refs:
        messages.append(
            f"evidence[{index}].client_ref conflicts with existing evidence ref: {client_ref}"
        )
        valid = False
    if not client_ref.startswith("llm:"):
        messages.append(f"evidence[{index}].client_ref must start with llm:")
        valid = False
    if client_ref in seen_client_refs:
        messages.append(f"Duplicate evidence client_ref: {client_ref}")
        valid = False
    seen_client_refs.add(client_ref)
    if valid:
        return client_ref
    return None


def _validate_evidence_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> set[str]:
    """校验证据条目并收集可供本次 finding/risk 引用的新 client_ref。

    Args: content 为规范化分析结果；allowed_evidence_refs 为已有证据 ID；messages 收集错误。
    Returns: 通过 client_ref 规则的新引用集合；其他字段错误仍可能导致整体被拒绝。
    Raises: content 缺少 evidence 或集合不可迭代时抛出异常。
    副作用：可能补齐条目的 metadata，并追加诊断；由校验入口调用。
    """
    # seen_client_refs 全局去重：记录本次输出里所有出现过的 `client_ref`不管是否有效。
    seen_client_refs: set[str] = set()
    # payload_llm_evidence_refs **合法新增入库**：只存完全合格的、LLM 自己新增的证据 ID。
    payload_llm_evidence_refs: set[str] = set()
    for index, item in _validate_items_are_objects(content, "evidence", messages):
        for field in ["client_ref", "title", "summary", "evidence_type"]:
            _validate_required_string(item, "evidence", index, field, messages)
        payload_ref = _validate_evidence_client_ref(
            item,
            index,
            allowed_evidence_refs,
            seen_client_refs,
            messages,
        )
        if payload_ref is not None:
            payload_llm_evidence_refs.add(payload_ref)
        _validate_optional_confidence(item, "evidence", index, messages)
        _validate_optional_metadata(item, "evidence", index, messages)
    return payload_llm_evidence_refs


def _validate_finding_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> None:
    """检查 findings 的必要文字字段、confidence、证据引用和 metadata。

    Args: content 为规范化分析结果；allowed_evidence_refs 含已有及本次新增证据 ID；messages 收集错误。
    Returns: None；是否通过由最终 messages 是否为空决定。
    Raises: content 缺少 findings 或集合不可迭代时抛出异常。
    副作用：可能补齐条目的 metadata，或追加诊断；由校验入口调用。
    """
    for index, item in _validate_items_are_objects(content, "findings", messages):
        for field in ["title", "summary", "category", "impact_level"]:
            _validate_required_string(item, "findings", index, field, messages)
        _validate_optional_confidence(item, "findings", index, messages)
        _validate_evidence_refs(item, "findings", index, allowed_evidence_refs, messages)
        _validate_optional_metadata(item, "findings", index, messages)


def _validate_risk_items(
    content: dict[str, Any], allowed_evidence_refs: set[str], messages: list[str]
) -> None:
    """检查 risks 的标题、说明、严重程度、缓解措施和证据引用。

    Args: content 为规范化分析结果；allowed_evidence_refs 含已有及本次新增证据 ID；messages 收集错误。
    Returns: None；是否通过由最终 messages 是否为空决定。
    Raises: content 缺少 risks 或集合不可迭代时抛出异常。
    副作用：可能补齐条目的 metadata，或追加诊断；由校验入口调用。
    """
    for index, item in _validate_items_are_objects(content, "risks", messages):
        for field in ["title", "summary", "severity", "mitigation"]:
            _validate_required_string(item, "risks", index, field, messages)
        _validate_evidence_refs(item, "risks", index, allowed_evidence_refs, messages)
        _validate_optional_metadata(item, "risks", index, messages)


def _validate_question_items(content: dict[str, Any], messages: list[str]) -> None:
    """检查问题条目的 prompt、reason、impact 及可选 status 字段。

    Args: content 为规范化分析结果；messages 收集字段错误。
    Returns: None；是否通过由最终 messages 是否为空决定。
    Raises: content 缺少 questions 或集合不可迭代时抛出异常。
    副作用：可能补齐条目的 metadata，或追加诊断；由校验入口调用。
    """
    for index, item in _validate_items_are_objects(content, "questions", messages):
        for field in ["prompt", "reason", "impact"]:
            _validate_required_string(item, "questions", index, field, messages)
        _validate_optional_string(item, "questions", index, "status", messages)
        _validate_optional_metadata(item, "questions", index, messages)


def _validate_recommendation_items(content: dict[str, Any], messages: list[str]) -> None:
    """检查建议的正文、理由、confidence 及可选步骤列表。

    Args: content 为规范化分析结果；messages 收集字段错误。
    Returns: None；是否通过由最终 messages 是否为空决定。
    Raises: content 缺少 recommendations 或集合不可迭代时抛出异常。
    副作用：可能补齐条目的 metadata，或追加诊断；由校验入口调用。
    """
    for index, item in _validate_items_are_objects(content, "recommendations", messages):
        for field in ["summary", "rationale"]:
            _validate_required_string(item, "recommendations", index, field, messages)
        _validate_optional_confidence(item, "recommendations", index, messages)
        if "next_steps" in item and (
            not isinstance(item["next_steps"], list)
            or any(not isinstance(step, str) for step in item["next_steps"])
        ):
            messages.append(
                f"{_message_prefix('recommendations', index, 'next_steps')} must be a list of strings"
            )
        _validate_optional_metadata(item, "recommendations", index, messages)


def validate_llm_analysis_output(
    payload: dict[str, Any], allowed_evidence_refs: set[str]
) -> LLMAnalysisValidation:
    """校验 LLM 分析输出的集合结构、字段类型和 evidence 引用关系。

    Args:
        payload 为模型返回的 JSON 对象，例如：
            {"evidence": [...], "findings": [...], "risks": [...],
             "questions": [...], "recommendations": [...],
             "reasoning_summary": "...", "coverage_notes": [...]}
        allowed_evidence_refs 为当前项目已有的证据引用，如 {"evidence:source-1"}；
            LLM 新增证据的引用必须以 `llm:` 开头且不能与其冲突。
    Returns:
        LLMAnalysisValidation；accepted 表示是否通过，content 是规范化副本，
        validation 含 passed 和 messages 诊断信息。
    Raises: payload 不是字典或内部结构不可遍历时可能抛出类型异常。
    副作用：不访问外部系统；复制并规范化输入，供分析合并流程调用。
    """
    # messages 收集所有结构和引用错误，最终决定 accepted 是否为 True。
    messages: list[str] = []
    # content 是复制并规范化后的模型结果，后续 merge 只使用它而不直接使用原始 payload。
    content: dict[str, Any] = {}

    for key in REQUIRED_COLLECTION_KEYS:
        content[key] = _normalize_collection(payload, key, messages, required=True)

    content["reasoning_summary"] = _reasoning_summary(payload)
    content["coverage_notes"] = _normalize_collection(payload, "coverage_notes", messages)

    # payload_llm_evidence_refs 只通过引用规则；条目其他字段的错误仍记录在 messages 中。
    payload_llm_evidence_refs = _validate_evidence_items(content, allowed_evidence_refs, messages)
    # merged_allowed_refs 允许 finding/risk 引用已有证据或本次 LLM 新增的证据。
    merged_allowed_refs = allowed_evidence_refs | payload_llm_evidence_refs
    _validate_finding_items(content, merged_allowed_refs, messages)
    _validate_risk_items(content, merged_allowed_refs, messages)
    _validate_question_items(content, messages)
    _validate_recommendation_items(content, messages)

    # validation 保留机器可判断的通过状态和完整诊断文本，便于审计和降级处理。
    validation = {"passed": not messages, "messages": messages}
    # 返回 accepted、规范化 content 和 validation 三部分，调用方据此决定是否合并。
    return LLMAnalysisValidation(
        accepted=validation["passed"],
        content=content,
        validation=validation,
    )
