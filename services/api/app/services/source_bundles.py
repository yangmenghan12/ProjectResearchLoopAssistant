from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.security import SourceValidationError, validate_source_input
from app.db.models import Project, Source, SourceBundle, SourceBundleItem
from app.schemas.source_bundle import SourceBundleCreate, SourceBundleItemCreate
from app.services.audit import record_event

SOURCE_BUNDLE_INPUT_TYPES = {
    "github_repo": "github_repo",
    "local_directory": "local_directory",
    "local_file": "local_file",
    "web_page": "web_url",
    "markdown_file": "local_file",
    "pdf_file": "local_file",
}
COLLECTION_SUCCESS_STATUSES = {"collected", "skipped"}
COLLECTION_TERMINAL_STATUSES = COLLECTION_SUCCESS_STATUSES | {"failed"}


def create_source_bundle(
    session: Session,
    project: Project,
    payload: SourceBundleCreate,
) -> SourceBundle:
    """创建资料包，校验每个输入并登记可用来源，同时保留失败项。

    Args: session 为数据库会话；project 为项目；payload 包含资料包名称和 1–20 个输入项。
    Returns: 已加入 session 的 SourceBundle。
    Raises: 输入校验异常不会中断单项处理；数据库异常向上抛出。
    副作用：写入 bundle、items、Source 和审计事件；由 source bundle API 调用。
    """
    bundle = SourceBundle(
        project_id=project.id,
        name=payload.name,
        status="pending",
        metadata_json={},
    )
    session.add(bundle)
    session.flush()

    source_count = 0
    failed_count = 0
    item_summaries: list[dict] = []
    for item_payload in payload.items:
        item, source_created = build_source_bundle_item(session, project, bundle, item_payload)
        session.add(item)
        session.flush()
        if item.source is not None:
            item.source.metadata_json = {
                **item.source.metadata_json,
                "bundle_id": bundle.id,
                "bundle_item_id": item.id,
            }
        if source_created:
            source_count += 1
        else:
            failed_count += 1
        item_summaries.append(
            {
                "input_type": item.input_type,
                "status": item.status,
                "source_id": item.source_id,
                "normalized_uri": item.normalized_uri,
                "error_message": item.error_message,
            }
        )

    item_count = len(payload.items)
    bundle.status = resolve_bundle_status(item_count, source_count)
    bundle.metadata_json = {
        "item_count": item_count,
        "source_count": source_count,
        "failed_count": failed_count,
    }
    session.flush()
    record_event(
        session,
        event_type="source_bundle.created",
        message="Source bundle created",
        project_id=project.id,
        payload={
            "bundle_id": bundle.id,
            "status": bundle.status,
            "item_count": item_count,
            "source_count": source_count,
            "failed_count": failed_count,
            "items": item_summaries,
        },
    )
    return bundle


def build_source_bundle_item(
    session: Session,
    project: Project,
    bundle: SourceBundle,
    item_payload: SourceBundleItemCreate,
) -> tuple[SourceBundleItem, bool]:
    """将单个资料包输入映射为 SourceBundleItem，并在有效时创建 Source。

    Args: session 为数据库会话；project/bundle 为所属实体；item_payload 为单项输入。
    Returns: `(item, source_created)`，失败项不关联 Source。
    Raises: 未预期的数据库异常向上抛出；来源校验错误转为 failed item。
    副作用：可能向 session 添加 Source；由 `create_source_bundle` 调用。
    """
    # input_type 是资料包 API 使用的输入类型；source_type 是采集器实际识别的来源类型。
    input_type = item_payload.input_type
    source_type = SOURCE_BUNDLE_INPUT_TYPES.get(input_type, input_type)
    base_metadata = {
        "input_type": input_type,
        "source_type": source_type,
    }
    # validated 同时提供规范化 URI 和采集阶段需要的 metadata。
    try:
        validated = validate_source_input(source_type, item_payload.raw_value)
    except SourceValidationError as exc:
        # 校验失败仍返回 item，便于资料包保留失败原因并继续处理其他输入。
        return (
            SourceBundleItem(
                bundle_id=bundle.id,
                project_id=project.id,
                input_type=input_type,
                raw_value=item_payload.raw_value,
                status="failed",
                error_message=str(exc),
                metadata_json=base_metadata,
            ),
            False,
        )

    metadata = {**base_metadata, **validated.metadata}
    source = Source(
        project_id=project.id,
        source_type=source_type,
        uri=item_payload.raw_value,
        normalized_uri=validated.normalized_uri,
        status="pending",
        metadata_json=metadata,
    )
    session.add(source)
    # 成功返回带关联 Source 的 pending item，采集器稍后更新其状态。
    return (
        SourceBundleItem(
            bundle_id=bundle.id,
            project_id=project.id,
            source=source,
            input_type=input_type,
            raw_value=item_payload.raw_value,
            normalized_uri=validated.normalized_uri,
            status="pending",
            metadata_json=metadata,
        ),
        True,
    )


def resolve_bundle_status(item_count: int, source_count: int) -> str:
    """根据输入总数和成功创建的来源数计算资料包初始状态。

    Args: item_count 为输入项数量；source_count 为通过校验的来源数量，均应为非负整数。
    Returns: `ready`、`partial` 或 `failed` 状态字符串。
    Raises: 不主动校验负数；异常输入可能产生不符合业务预期的状态。
    副作用：无；由 `create_source_bundle` 在入库前调用。
    """
    if source_count == item_count:
        return "ready"
    if source_count == 0:
        return "failed"
    return "partial"


def refresh_bundle_item_collection_state(session: Session, bundle: SourceBundle) -> SourceBundle:
    """根据关联 Source 的终态刷新资料包及 item 状态。

    Args: session 为数据库会话；bundle 为已加载 items/source 的资料包。
    Returns: 更新后的 SourceBundle。
    Raises: 数据库 flush 异常向上抛出。
    副作用：修改 item/bundle 状态并 flush；由 bundle 采集接口调用。
    """
    for item in bundle.items:
        if item.source is not None and item.source.status in COLLECTION_TERMINAL_STATUSES:
            item.status = item.source.status

    # item_statuses 反映资料包中所有输入，source_item_statuses 只反映已创建 Source 的输入。
    item_statuses = [item.status for item in bundle.items]
    source_item_statuses = [
        item.status for item in bundle.items if item.source is not None
    ]

    if item_statuses and all(status == "failed" for status in item_statuses):
        bundle.status = "failed"
    elif (
        source_item_statuses
        and all(status in COLLECTION_SUCCESS_STATUSES for status in source_item_statuses)
        and "failed" not in item_statuses
    ):
        bundle.status = "collected"
    elif any(status in COLLECTION_SUCCESS_STATUSES for status in item_statuses):
        bundle.status = "partial"

    session.flush()
    # 返回已刷新并 flush 的 bundle；事务提交仍由调用方负责。
    return bundle


def collectable_source_ids(bundle: SourceBundle) -> set[str]:
    """只采集当前资料包里尚未进入终态的源，避免跨资料包副作用和重复快照。"""

    return {
        item.source_id
        for item in bundle.items
        if item.source_id is not None
        and item.source is not None
        and item.source.status not in COLLECTION_TERMINAL_STATUSES
    }


def list_source_bundles(session: Session, project: Project) -> list[SourceBundle]:
    """按创建顺序读取项目下的资料包及其条目。

    Args: session 为数据库会话；project 为目标项目。
    Returns: 按创建时间和 ID 排序的 SourceBundle 列表。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库并预加载 items；由资料包列表 API 调用。
    """
    statement = (
        select(SourceBundle)
        .options(selectinload(SourceBundle.items))
        .where(SourceBundle.project_id == project.id)
        .order_by(SourceBundle.created_at.asc(), SourceBundle.id.asc())
    )
    return list(session.scalars(statement).all())


def get_source_bundle(
    session: Session,
    project: Project,
    bundle_id: str,
) -> SourceBundle | None:
    """按项目边界读取资料包及条目，用于详情展示。

    Args: session 为数据库会话；project 为所属项目；bundle_id 为资料包 ID。
    Returns: 匹配的 SourceBundle；不存在或不属于项目时为 None。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库并预加载 items；由资料包详情 API 调用。
    """
    statement = (
        select(SourceBundle)
        .options(selectinload(SourceBundle.items))
        .where(SourceBundle.id == bundle_id, SourceBundle.project_id == project.id)
    )
    return session.scalar(statement)


def get_source_bundle_for_collection(
    session: Session,
    project: Project,
    bundle_id: str,
) -> SourceBundle | None:
    """读取资料包及关联 Source，为采集状态聚合准备完整关系数据。

    Args: session 为数据库会话；project 为所属项目；bundle_id 为资料包 ID。
    Returns: 匹配的 SourceBundle；不存在或不属于项目时为 None。
    Raises: 数据库查询异常向上抛出。
    副作用：仅读取数据库并预加载 items/source；由采集流程调用。
    """
    statement = (
        select(SourceBundle)
        .options(selectinload(SourceBundle.items).selectinload(SourceBundleItem.source))
        .where(SourceBundle.id == bundle_id, SourceBundle.project_id == project.id)
    )
    return session.scalar(statement)
