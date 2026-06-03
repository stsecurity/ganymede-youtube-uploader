import argparse
import asyncio

from sqlalchemy import select

from .db import SessionLocal, init_db
from .jobs import JobProcessor, create_or_update_job
from .models import JobStatus, UploadJob
from .ui_settings import build_effective_settings


async def run_command(args: argparse.Namespace) -> None:
    init_db()
    session = SessionLocal()
    try:
        if args.command == "status":
            jobs = session.scalars(
                select(UploadJob).order_by(UploadJob.created_at.desc()).limit(20)
            )
            for job in jobs:
                print(
                    f"{job.id}\t{job.status.value}\t"
                    f"{job.ganymede_vod_id or '-'}\t{job.youtube_video_id or '-'}"
                )
            return
        processor = JobProcessor(build_effective_settings(session))
        if args.command == "enqueue-ganymede-vod":
            job = create_or_update_job(session, ganymede_vod_id=args.vod_id)
            await processor.process(session, job)
        elif args.command == "enqueue-external-id":
            job = create_or_update_job(session, external_id=args.external_id)
            await processor.process(session, job)
        else:
            job = session.get(UploadJob, args.job_id)
            if not job:
                raise SystemExit(f"Job not found: {args.job_id}")
            if args.command == "retry":
                job.status = JobStatus.RECEIVED
                job.last_error = None
                session.commit()
                await processor.process(session, job)
            elif args.command == "verify":
                await processor.verify_and_cleanup(session, job)
            elif args.command == "cleanup":
                await processor.cleanup_only(session, job)
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    retry = sub.add_parser("retry")
    retry.add_argument("job_id", type=int)
    enqueue_vod = sub.add_parser("enqueue-ganymede-vod")
    enqueue_vod.add_argument("vod_id")
    enqueue_external = sub.add_parser("enqueue-external-id")
    enqueue_external.add_argument("external_id")
    verify = sub.add_parser("verify")
    verify.add_argument("job_id", type=int)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("job_id", type=int)
    return parser


def main() -> None:
    asyncio.run(run_command(build_parser().parse_args()))


if __name__ == "__main__":
    main()
