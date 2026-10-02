"""Application answer bank and questionnaire endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from jobpilot import services
from jobpilot.pipeline.answer import AnswerResult
from jobpilot.services import AnswerBankItem

router = APIRouter(prefix="/api", tags=["answers"])


class AskQuestionRequest(BaseModel):
    question: str
    job_id: int | None = None


class SetAnswerRequest(BaseModel):
    answer_text: str


class ActionSuccessResponse(BaseModel):
    success: bool = True


@router.post("/answers/ask", response_model=AnswerResult)
def ask_question(body: AskQuestionRequest, request: Request) -> AnswerResult:
    """Generate or retrieve an answer to an application question."""
    config = getattr(request.app.state, "config", None)
    return services.answer_question(
        question=body.question,
        job_id=body.job_id,
        config=config,
    )


@router.get("/answers", response_model=list[AnswerBankItem])
def list_answers() -> list[AnswerBankItem]:
    """List all questions and stored answers in the answer bank."""
    return services.list_answers()


@router.put("/answers/{answer_id}", response_model=ActionSuccessResponse)
def update_answer(answer_id: int, body: SetAnswerRequest) -> ActionSuccessResponse:
    """Supply or update an answer for an answer bank item."""
    updated = services.set_answer(answer_id=answer_id, text=body.answer_text)
    if not updated:
        raise KeyError(f"Answer {answer_id} not found.")
    return ActionSuccessResponse(success=True)


@router.post("/answers/{answer_id}/approve", response_model=ActionSuccessResponse)
def approve_answer(answer_id: int) -> ActionSuccessResponse:
    """Mark an answer bank entry as approved."""
    approved = services.approve_answer(answer_id=answer_id)
    if not approved:
        raise KeyError(f"Answer {answer_id} not found.")
    return ActionSuccessResponse(success=True)
