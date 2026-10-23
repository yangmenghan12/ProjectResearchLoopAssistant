from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EvaluationRun
from app.db.session import get_session
from app.evaluations.runner import run_evaluations
from app.schemas.evaluation import EvaluationRunRead

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post("/run", response_model=EvaluationRunRead, status_code=status.HTTP_201_CREATED)
def run_evaluation_harness(session: Session = Depends(get_session)) -> EvaluationRun:
    """在隔离 workspace 中运行固定 evaluation cases 并保存汇总结果。

    Args: session 为数据库会话。
    Returns: EvaluationRun；状态和 case 结果已写入数据库。
    Raises: evaluation 执行或 fixture 生成异常向上抛出。
    副作用：读写临时 workspace、扫描 fixture 并写入数据库；由 evaluation 接口调用。
    """
    return run_evaluations(session)


@router.get("/latest", response_model=EvaluationRunRead)
def get_latest_evaluation(session: Session = Depends(get_session)) -> EvaluationRun:
    """返回最近一次 evaluation run，供回归页面展示。

    Args: session 为数据库会话。
    Returns: 最新的 EvaluationRun。
    Raises: 没有历史记录时返回 HTTP 404。
    副作用：仅读取数据库；由 evaluation 页面调用。
    """
    evaluation = session.scalar(
        select(EvaluationRun).order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
    )
    if evaluation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation run not found")
    return evaluation
