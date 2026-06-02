from ganymede_youtube_uploader.models import JobStatus, UploadJob
from ganymede_youtube_uploader.worker import recover_interrupted_jobs


def test_recover_interrupted_jobs_resumes_safe_statuses(session) -> None:
    received = UploadJob(ganymede_vod_id="vod-1", status=JobStatus.RECEIVED)
    uploaded = UploadJob(
        ganymede_vod_id="vod-2",
        youtube_video_id="yt-2",
        status=JobStatus.UPLOADED,
    )
    session.add_all([received, uploaded])
    session.commit()

    job_ids = recover_interrupted_jobs(session)

    assert set(job_ids) == {received.id, uploaded.id}


def test_recover_interrupted_jobs_blocks_unsafe_upload_resume(session) -> None:
    uploading = UploadJob(ganymede_vod_id="vod-1", status=JobStatus.UPLOADING)
    session.add(uploading)
    session.commit()

    job_ids = recover_interrupted_jobs(session)

    assert job_ids == []
    assert uploading.status == JobStatus.NEEDS_MANUAL_REVIEW
    assert "avoid uploading the same VOD twice" in uploading.last_error


def test_recover_interrupted_jobs_resumes_uploading_with_saved_youtube_id(session) -> None:
    uploading = UploadJob(
        ganymede_vod_id="vod-1",
        youtube_video_id="yt-1",
        status=JobStatus.UPLOADING,
    )
    session.add(uploading)
    session.commit()

    job_ids = recover_interrupted_jobs(session)

    assert job_ids == [uploading.id]
