from __future__ import annotations

import argparse
import time
from uuid import uuid4

from app.db.session import get_worker_session_factory
from app.services.run_jobs import recover_stale_jobs
from app.workers.run_worker import run_one_job


def run_worker_loop(worker_id: str, queue_name: str, poll_interval: float, lease_seconds: int) -> None:
    """持续轮询队列并执行 job，直到进程收到终止信号。

    Args: worker_id/queue_name 为非空标识；poll_interval、lease_seconds 必须为正数。
    Returns: None；函数通常作为常驻进程入口运行。
    Raises: 未处理的配置或数据库异常可能终止 worker 进程。
    副作用：持续访问数据库、执行 run 并提交状态；由 `main` 调用。
    """
    session_factory = get_worker_session_factory()
    while True:
        with session_factory() as session:
            recover_stale_jobs(session)
            session.commit()
        did_work = run_one_job(worker_id=worker_id, queue_name=queue_name, lease_seconds=lease_seconds)
        if not did_work:
            time.sleep(poll_interval)


def main() -> None:
    """读取命令行参数并启动默认 run worker 进程。

    Args: 无。
    Returns: None；正常情况下不会返回。
    Raises: 配置解析或 worker 初始化异常向上抛出。
    副作用：启动常驻轮询和数据库 IO；由命令行模块调用。
    """
    parser = argparse.ArgumentParser(description="Loop Engineering local worker service")
    parser.add_argument("--worker-id", default=f"local-worker-{uuid4()}")
    parser.add_argument("--queue", default="default")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    args = parser.parse_args()
    run_worker_loop(args.worker_id, args.queue, args.poll_interval, args.lease_seconds)


if __name__ == "__main__":
    main()
