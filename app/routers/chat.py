from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import ollama_chat
from app.services.errors import InvalidInputError, NotFoundError

router = APIRouter(
    prefix="/api/chat",
    tags=["ollama-chat"],
    dependencies=[Depends(require_admin)],
)


@router.post(
    "/",
    response_model=schemas.FactoryDescriptionJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def chat(request: schemas.FactoryChatRequest, db: Session = Depends(get_db)):
    try:
        job = ollama_chat.enqueue_description_job(db, request.factory_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidInputError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return schemas.FactoryDescriptionJobAccepted(
        id=job.id,
        factory_id=job.factory_id,
        status="queued",
        status_url=f"/api/chat/{job.factory_id}/jobs/{job.id}",
    )


@router.get(
    "/{factory_id}/jobs/{job_id}",
    response_model=schemas.FactoryDescriptionJobResponse,
)
def get_description_job(
    factory_id: int, job_id: int, db: Session = Depends(get_db),
):
    try:
        return ollama_chat.get_description_job(db, factory_id, job_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
