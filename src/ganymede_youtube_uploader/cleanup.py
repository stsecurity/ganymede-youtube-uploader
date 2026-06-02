from .ganymede_client import GanymedeClient, GanymedeClientError
from .models import JobStatus, UploadJob


async def cleanup_ganymede(job: UploadJob, client: GanymedeClient) -> JobStatus:
    if job.status not in {JobStatus.VERIFIED, JobStatus.CLEANING_GANYMEDE}:
        raise ValueError("Cleanup is allowed only after YouTube verification succeeds")
    if not job.ganymede_vod_id:
        raise ValueError("Cannot cleanup without a Ganymede VOD id")
    try:
        await client.delete_vod(job.ganymede_vod_id, delete_files=True)
    except GanymedeClientError as exc:
        job.last_error = f"Ganymede cleanup failed: {exc}"
        return JobStatus.NEEDS_MANUAL_CLEANUP
    job.last_error = None
    return JobStatus.COMPLETED
