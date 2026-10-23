from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import ollama_chat
from app.services.errors import NotFoundError

router = APIRouter(
    prefix="/api/chat",
    tags=["ollama-chat"],
    dependencies=[Depends(require_admin)],
)


@router.post("/", response_model=schemas.FactoryChatResponse)
async def chat(request: schemas.FactoryChatRequest, db: Session = Depends(get_db)):
    try:
        return await ollama_chat.chat(db, request.factory_id, request.prompt)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ollama_chat.OllamaUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="تعذر الحصول على رد صالح من نموذج Ollama",
        ) from exc
