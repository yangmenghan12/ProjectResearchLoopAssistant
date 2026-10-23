import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.services.reports import build_analysis_package
from app.verifiers.package import as_list, latest_item, value_of

COLLECTIONS = ["findings", "risks", "questions", "recommendations"]
SNAPSHOT_COLLECTIONS = [
    "turns",
    "evidence_items",
    *COLLECTIONS,
    "quality_scores",
]

IDENTITY_FIELDS = ("title", "prompt", "summary")
# 这些字段由数据库或单次 run 生成，会随版本变化；diff 的 changed 只比较业务内容。
VOLATILE_FIELDS = {
    "id",
    "project_id",
    "run_id",
    "turn_id",
    "created_at",
    "updated_at",
    "started_at",
    "completed_at",
    "answered_at",
    "evidence_refs_json",
}


def build_report_package_snapshot(session: Session, report: Any) -> dict[str, Any]:
    """按报告创建时间截取分析包，避免把之后产生的数据误计入报告 diff。

    Args: session 为数据库会话；report 为包含 run 和 created_at 的报告实体。
    Returns: 可 JSON 序列化的报告快照。
    Raises: 构建分析包或数据库访问异常向上抛出。
    副作用：仅读取数据库；由报告差异 API 调用。
    """
    package = build_analysis_package(session, value_of(report, "run"))
    cutoff = value_of(report, "created_at")
    if cutoff is None:
        return package

    snapshot = dict(package)
    for collection in SNAPSHOT_COLLECTIONS:
        snapshot[collection] = [
            item
            for item in as_list(value_of(package, collection, []))
            if created_at_before_or_equal(item, cutoff)
        ]
    return snapshot


def diff_collection(before: Any, after: Any) -> dict[str, list[dict[str, Any]]]:
    """按稳定身份键比较两个集合，分别返回新增、删除和内容变化项。

    Args: before/after 为可迭代的字典或 ORM 项集合。
    Returns: 含 `added`、`removed`、`changed` 三类项的字典。
    Raises: 项目无法转换为快照时向上抛出异常。
    副作用：无；由 `diff_report_snapshots` 调用。
    """
    # 两个索引把原始集合转换成稳定键到快照的映射，避免直接比较 ORM 对象。
    before_index = index_collection(before)
    after_index = index_collection(after)
    before_keys = set(before_index)
    after_keys = set(after_index)

    added = [after_index[key] for key in sorted(after_keys - before_keys)]
    removed = [before_index[key] for key in sorted(before_keys - after_keys)]
    changed = [
        {
            "key": key,
            "before": before_index[key],
            "after": after_index[key],
        }
        for key in sorted(before_keys & after_keys)
        if before_index[key] != after_index[key]
    ]
    # 返回三类差异：新增项、删除项以及同一身份键下内容发生变化的项。
    return {"added": added, "removed": removed, "changed": changed}


def diff_report_snapshots(before: Any, after: Any) -> dict[str, Any]:
    """汇总两个报告快照的质量分数变化及核心业务集合差异。

    Args: before/after 为报告分析包快照。
    Returns: 含质量分数和 findings、risks、questions、recommendations diff 的字典。
    Raises: 快照结构不兼容时向上抛出异常。
    副作用：无；由报告差异入口调用。
    """
    quality_before = latest_quality_score(before)
    quality_after = latest_quality_score(after)
    quality_delta = None if quality_before is None or quality_after is None else quality_after - quality_before
    # diff 先记录质量分数变化，再为每个业务集合追加结构化差异。
    diff: dict[str, Any] = {
        "quality_before": quality_before,
        "quality_after": quality_after,
        "quality_delta": quality_delta,
    }
    for collection in COLLECTIONS:
        diff[collection] = diff_collection(value_of(before, collection, []), value_of(after, collection, []))
    # 返回可直接作为 API JSON 的报告差异对象。
    return diff


def latest_quality_score(package: Any) -> int | None:
    """从分析包中读取最新质量评分，缺少评分时返回 None。

    Args: package 为分析包字典或兼容对象。
    Returns: `overall_score` 整数，或 None。
    Raises: 非法分数转换异常向上抛出。
    副作用：无；由报告快照比较调用。
    """
    score = latest_item(value_of(package, "quality_scores", []))
    if score is None:
        return None
    value = value_of(score, "overall_score")
    return int(value) if value is not None else None


def created_at_before_or_equal(item: Any, cutoff: datetime) -> bool:
    """判断集合项是否在报告截点之前，缺少时间戳时保守地纳入快照。

    Args: item 为集合项；cutoff 为报告创建时间。
    Returns: 时间不晚于 cutoff 或缺少时间戳时为 True。
    Raises: 时间值类型不兼容时可能向上抛出。
    副作用：无；由 `build_report_package_snapshot` 调用。
    """
    created_at = value_of(item, "created_at")
    if not isinstance(created_at, datetime):
        return True
    return comparable_datetime(created_at) <= comparable_datetime(cutoff)


def comparable_datetime(value: datetime) -> datetime:
    """把 naive 或带时区时间统一转换为 UTC，供安全比较。

    Args: value 为 datetime。
    Returns: 带 UTC 时区的 datetime。
    Raises: value 不是 datetime 时由调用方类型错误暴露。
    副作用：无；由时间截点判断调用。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def index_collection(items: Any) -> dict[str, dict[str, Any]]:
    """将集合转为稳定键到快照的索引，并为重复身份键追加序号。

    Args: items 为可迭代集合，元素可为字典或 ORM 对象。
    Returns: 用于 diff 的键到 JSON 兼容快照映射。
    Raises: 元素无法读取属性时向上抛出异常。
    副作用：无；由 `diff_collection` 调用。
    """
    indexed: dict[str, dict[str, Any]] = {}
    key_counts: dict[str, int] = {}
    for item in as_list(items):
        snapshot = snapshot_item(item)
        # 同一轮报告里理论上不应出现同名条目；这里保留序号，避免重复标题互相覆盖。
        base_key = identity_key(item, snapshot)
        key_counts[base_key] = key_counts.get(base_key, 0) + 1
        key = base_key if key_counts[base_key] == 1 else f"{base_key}#{key_counts[base_key]}"
        indexed[key] = snapshot
    return indexed


def identity_key(item: Any, snapshot: dict[str, Any]) -> str:
    """从业务标题字段、实体 ID 或完整快照生成稳定比较键。

    Args: item 为原始集合项；snapshot 为已清理的快照字典。
    Returns: 可用于跨报告匹配的字符串键。
    Raises: 快照无法 JSON 编码时由 fallback 序列化逻辑处理为字符串。
    副作用：无；由 `index_collection` 调用。
    """
    for field in IDENTITY_FIELDS:
        value = snapshot.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    item_id = value_of(item, "id")
    if item_id not in (None, ""):
        return f"id:{item_id}"
    return f"item:{json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)}"


def snapshot_item(item: Any) -> dict[str, Any]:
    """提取集合项的业务字段，去除数据库 ID 和时间等易变字段。

    Args: item 为字典、ORM 实体或普通对象。
    Returns: 不含易变字段且值可 JSON 化的快照。
    Raises: ORM 属性读取异常向上抛出。
    副作用：无；由索引和 diff 流程调用。
    """
    if isinstance(item, dict):
        pairs = item.items()
    elif hasattr(item, "__table__"):
        pairs = ((column.name, value_of(item, column.name)) for column in item.__table__.columns)
    else:
        pairs = vars(item).items() if hasattr(item, "__dict__") else [("value", item)]

    snapshot: dict[str, Any] = {}
    for key, value in pairs:
        if key.startswith("_") or key in VOLATILE_FIELDS:
            continue
        snapshot[key] = jsonable_value(value)
    return snapshot


def jsonable_value(value: Any) -> Any:
    """递归把日期、Decimal 和容器值转换为 JSON 兼容表示。

    Args: value 为任意业务字段值。
    Returns: 标量、列表、字典或字符串形式的可序列化值。
    Raises: 无显式异常；未知对象通过 `str` 降级。
    副作用：无；由 `snapshot_item` 调用。
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable_value(inner) for inner in value]
    return str(value)
