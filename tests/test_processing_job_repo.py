import asyncio

import pytest

from app.core.config import Settings
from app.models.processing_job import (
    ProcessingJobCreate,
    ProcessingJobStage,
    ProcessingJobStatus,
    ProcessingJobType,
)
from app.repositories.processing_job_repo import ProcessingJobRepository
from tests.test_processing_job_repo_fakes import _FakeMongoManager


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setenv("APP__SECRET_KEY", "supersecret")
    return ProcessingJobRepository(_FakeMongoManager(), Settings())


def test_create_and_get_job(repo):
    payload = ProcessingJobCreate(
        document_id="doc-1",
        company_id="reliance_industries",
        requested_by="admin-1",
        job_type=ProcessingJobType.DOCUMENT_PROCESSING,
        status=ProcessingJobStatus.QUEUED,
        stage=ProcessingJobStage.PARSE_PENDING,
    )
    created = asyncio.run(repo.create(payload, job_id="job-1"))
    assert created.job_id == "job-1"
    assert created.status == ProcessingJobStatus.QUEUED.value
    assert created.stage == ProcessingJobStage.PARSE_PENDING.value

    loaded = asyncio.run(repo.get_by_id("job-1"))
    assert loaded is not None
    assert loaded.document_id == "doc-1"


def test_find_active_job_by_document_id(repo):
    payload = ProcessingJobCreate(
        document_id="doc-1",
        company_id="reliance_industries",
        requested_by="admin-1",
        status=ProcessingJobStatus.QUEUED,
        stage=ProcessingJobStage.PARSE_PENDING,
    )
    asyncio.run(repo.create(payload, job_id="job-active"))

    active = asyncio.run(repo.find_active_job_by_document_id("doc-1"))
    assert active is not None
    assert active.job_id == "job-active"

    completed_payload = ProcessingJobCreate(
        document_id="doc-2",
        company_id="reliance_industries",
        requested_by="admin-1",
        status=ProcessingJobStatus.COMPLETED,
        stage=ProcessingJobStage.PARSE_COMPLETED,
    )
    asyncio.run(repo.create(completed_payload, job_id="job-done"))
    assert asyncio.run(repo.find_active_job_by_document_id("doc-2")) is None


def test_list_jobs_filters(repo):
    payload = ProcessingJobCreate(
        document_id="doc-1",
        company_id="reliance_industries",
        requested_by="admin-1",
    )
    asyncio.run(repo.create(payload, job_id="job-a"))
    asyncio.run(
        repo.create(
            ProcessingJobCreate(
                document_id="doc-2",
                company_id="reliance_industries",
                requested_by="admin-1",
                status=ProcessingJobStatus.FAILED,
            ),
            job_id="job-b",
        )
    )

    all_jobs = asyncio.run(repo.list_jobs())
    assert len(all_jobs) == 2

    queued_only = asyncio.run(repo.list_jobs(status=ProcessingJobStatus.QUEUED))
    assert len(queued_only) == 1
    assert queued_only[0].job_id == "job-a"


def test_update_job(repo):
    payload = ProcessingJobCreate(
        document_id="doc-1",
        company_id="reliance_industries",
        requested_by="admin-1",
    )
    asyncio.run(repo.create(payload, job_id="job-update"))
    updated = asyncio.run(
        repo.update_job(
            "job-update",
            status=ProcessingJobStatus.RUNNING,
            stage=ProcessingJobStage.PARSE_RUNNING,
            error_code="old",
            error_message="old message",
        )
    )
    assert updated is not None
    assert updated.status == ProcessingJobStatus.RUNNING.value
    assert updated.stage == ProcessingJobStage.PARSE_RUNNING.value

    cleared = asyncio.run(
        repo.update_job(
            "job-update",
            status=ProcessingJobStatus.COMPLETED,
            stage=ProcessingJobStage.PARSE_COMPLETED,
            clear_errors=True,
        )
    )
    assert cleared is not None
    assert cleared.error_code is None
    assert cleared.error_message is None
