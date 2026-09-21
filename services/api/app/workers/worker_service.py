from __future__ import annotations

import argparse
import time
from uuid import uuid4

from app.db.session import get_worker_session_factory
from app.services.run_jobs import recover_stale_jobs
from app.workers.run_worker import run_one_job


def run_worker_loop(worker_id: str, queue_name: str, poll_interval: float, lease_seconds: int) -> None:
    session_factory = get_worker_session_factory()
    while True:
        with session_factory() as session:
            recover_stale_jobs(session)
            session.commit()
        did_work = run_one_job(worker_id=worker_id, queue_name=queue_name, lease_seconds=lease_seconds)
        if not did_work:
            time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Loop Engineering local worker service")
    parser.add_argument("--worker-id", default=f"local-worker-{uuid4()}")
    parser.add_argument("--queue", default="default")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--lease-seconds", type=int, default=60)
    args = parser.parse_args()
    run_worker_loop(args.worker_id, args.queue, args.poll_interval, args.lease_seconds)


if __name__ == "__main__":
    main()
