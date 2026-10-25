from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import social_channels as channel_service

router = APIRouter(
    prefix="/api/social-channels", tags=["social-channels"], dependencies=[Depends(require_admin)],
)


@router.post("/", response_model=schemas.SocialChannelResponse, status_code=status.HTTP_201_CREATED)
def create_channel(channel: schemas.SocialChannelCreate, db: Session = Depends(get_db)):
    return channel_service.create_channel(db, **channel.model_dump())


@router.get("/", response_model=List[schemas.SocialChannelResponse])
def get_channels(
    platform: Optional[schemas.ChannelPlatform] = Query(None),
    is_active: Optional[bool] = Query(None),
    factory_id: Optional[int] = Query(None, gt=0),
    db: Session = Depends(get_db),
):
    """القنوات مع تصفية اختيارية بالمصنع والمنصة والحالة."""
    return channel_service.list_channels(db, platform, is_active, factory_id)


@router.get("/{channel_id}", response_model=schemas.SocialChannelResponse)
def get_channel(channel_id: int, db: Session = Depends(get_db)):
    return channel_service.get_channel(db, channel_id)


@router.patch("/{channel_id}", response_model=schemas.SocialChannelResponse)
def update_channel(channel_id: int, update: schemas.SocialChannelUpdate, db: Session = Depends(get_db)):
    return channel_service.update_channel(db, channel_id, update.model_dump(exclude_unset=True))


@router.delete("/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel_service.delete_channel(db, channel_id)
    return {"status": "success", "message": "تم حذف القناة بنجاح"}
