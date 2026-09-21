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
    return {"added": added, "removed": removed, "changed": changed}


def diff_report_snapshots(before: Any, after: Any) -> dict[str, Any]:
    quality_before = latest_quality_score(before)
    quality_after = latest_quality_score(after)
    quality_delta = None if quality_before is None or quality_after is None else quality_after - quality_before
    diff: dict[str, Any] = {
        "quality_before": quality_before,
        "quality_after": quality_after,
        "quality_delta": quality_delta,
    }
    for collection in COLLECTIONS:
        diff[collection] = diff_collection(value_of(before, collection, []), value_of(after, collection, []))
    return diff


def latest_quality_score(package: Any) -> int | None:
    score = latest_item(value_of(package, "quality_scores", []))
    if score is None:
        return None
    value = value_of(score, "overall_score")
    return int(value) if value is not None else None


def created_at_before_or_equal(item: Any, cutoff: datetime) -> bool:
    created_at = value_of(item, "created_at")
    if not isinstance(created_at, datetime):
        return True
    return comparable_datetime(created_at) <= comparable_datetime(cutoff)


def comparable_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def index_collection(items: Any) -> dict[str, dict[str, Any]]:
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
    for field in IDENTITY_FIELDS:
        value = snapshot.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    item_id = value_of(item, "id")
    if item_id not in (None, ""):
        return f"id:{item_id}"
    return f"item:{json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)}"


def snapshot_item(item: Any) -> dict[str, Any]:
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
