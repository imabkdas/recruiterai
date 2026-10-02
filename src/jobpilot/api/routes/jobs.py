"""Job management, queue, preparation, and tracking endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jobpilot import services
from jobpilot.pipeline.prepare import PrepareResult
from jobpilot.pipeline.queue import NeedsJdItem, QueueItem
from jobpilot.services import (
    AddJDResult,
    ApplicationItem,
    CompileResumeResult,
    FollowUpItem,
    JobDetail,
    JobListItem,
    JobStatusUpdate,
    ResumeSource,
)

router = APIRouter(prefix="/api", tags=["jobs"])


class MarkJobRequest(BaseModel):
    status: str
    channel: str | None = None
    note: str | None = None
    referral_contact: str | None = None


class UpdateApplicationRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    applied_at: str | None = None
    url: str | None = None


class AddJDRequest(BaseModel):
    text: str = Field(..., max_length=200000)


class CompileResumeRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=200_000)


@router.get("/queue", response_model=list[QueueItem])
def get_queue(request: Request, size: int | None = Query(None)) -> list[QueueItem]:
    """Return ranked daily queue items."""
    config = getattr(request.app.state, "config", None)
    return services.get_queue(size=size, config=config)


@router.get("/needs-jd", response_model=list[NeedsJdItem])
def get_needs_jd(request: Request) -> list[NeedsJdItem]:
    """Return jobs awaiting full JD paste."""
    config = getattr(request.app.state, "config", None)
    return services.get_needs_jd(config=config)


@router.get("/jobs", response_model=list[JobListItem])
def list_jobs(
    location: str | None = Query(None),
    status: str | None = Query(None),
    posted_from: str | None = Query(None),
    posted_to: str | None = Query(None),
) -> list[JobListItem]:
    """List every searched job. Filters match location text, status, and posted date."""
    return services.list_searched_jobs(
        location=location,
        status=status,
        posted_from=posted_from,
        posted_to=posted_to,
    )


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job_detail(job_id: int) -> JobDetail:
    """Return complete job details including analysis, score, and drafts."""
    return services.get_job_detail(job_id=job_id)


@router.get("/resume/source", response_model=ResumeSource)
def get_base_resume_source() -> ResumeSource:
    """Return the LaTeX for the sidebar resume editor."""
    return services.get_base_resume_source()


@router.post("/resume/compile", response_model=CompileResumeResult)
def compile_base_resume(body: CompileResumeRequest) -> CompileResumeResult:
    """Compile the sidebar resume. This is not attached to a job."""
    return services.compile_base_resume(source=body.source)


@router.get("/resume.pdf")
def download_base_resume_pdf(download: bool = False) -> FileResponse:
    """Return the PDF last compiled from the sidebar editor."""
    path = services.base_resume_pdf_path()
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/jobs/{job_id}/resume/source", response_model=ResumeSource)
def get_resume_source(job_id: int) -> ResumeSource:
    """Return the LaTeX source for this job's resume editor."""
    return services.get_resume_source(job_id=job_id)


@router.post("/jobs/{job_id}/resume/compile", response_model=CompileResumeResult)
def compile_resume(job_id: int, body: CompileResumeRequest) -> CompileResumeResult:
    """Compile edited LaTeX into a PDF stored with this job."""
    return services.compile_job_resume(job_id=job_id, source=body.source)


@router.get("/jobs/{job_id}/resume.pdf")
def download_resume_pdf(job_id: int, download: bool = False) -> FileResponse:
    """Return the PDF last compiled for this job.

    The preview pane embeds this inline. ``download=1`` sends it as an attachment.
    """
    path = services.job_resume_pdf_path(job_id=job_id)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.post("/jobs/{job_id}/prepare", response_model=PrepareResult)
def prepare_job(job_id: int, request: Request) -> PrepareResult:
    """Prepare application draft materials for a job."""
    config = getattr(request.app.state, "config", None)
    return services.prepare_job(job_id=job_id, config=config)


@router.patch("/jobs/{job_id}/application", response_model=ApplicationItem)
def update_application(job_id: int, body: UpdateApplicationRequest) -> ApplicationItem:
    """Update status, applied date, notes, or job link from the Applications page."""
    return services.update_application(
        job_id,
        status=body.status,
        notes=body.notes,
        applied_at=body.applied_at,
        url=body.url,
    )


@router.post("/jobs/{job_id}/mark", response_model=JobStatusUpdate)
def mark_job(job_id: int, body: MarkJobRequest) -> JobStatusUpdate:
    """Transition a job's status and record application metadata."""
    return services.mark_job(
        job_id=job_id,
        status=body.status,
        channel=body.channel,
        note=body.note,
        referral_contact=body.referral_contact,
    )


@router.post("/jobs/{job_id}/skip", response_model=JobStatusUpdate)
def skip_job(job_id: int) -> JobStatusUpdate:
    """Mark a job as skipped."""
    return services.skip_job(job_id=job_id)


@router.post("/jobs/{job_id}/jd", response_model=AddJDResult)
def add_jd(job_id: int, body: AddJDRequest) -> AddJDResult:
    """Attach full job description text to a job."""
    return services.add_jd(job_id=job_id, text=body.text)


@router.get("/applications", response_model=list[ApplicationItem])
def list_applications(status: str | None = Query(None)) -> list[ApplicationItem]:
    """List every searched job, optionally filtered by tracker status."""
    return services.list_applications(status=status)


@router.get("/follow-ups", response_model=list[FollowUpItem])
def get_follow_ups(request: Request, days: int | None = Query(None)) -> list[FollowUpItem]:
    """List applied jobs where follow-up is due."""
    config = getattr(request.app.state, "config", None)
    return services.follow_ups_due(days=days, config=config)
