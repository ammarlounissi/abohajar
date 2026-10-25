"""Create approved, timer-ready posts from catalog products."""
from datetime import date, datetime, time
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.time import utc_now
from app.models import DeliveryStatus, PostApproval, PostStatus
from app.services.errors import InvalidInputError, NotFoundError
from app.services.post_templates import build_caption
from app.services.schedule_slots import generate_slots


def create_ready_posts(
    db: Session,
    factory_id: int,
    channel_ids: Sequence[int],
    *,
    skus: Optional[Sequence[str]] = None,
    category_codes: Optional[Sequence[str]] = None,
    times: Optional[Sequence[time]] = None,
    start_date: Optional[date] = None,
) -> List[models.Post]:
    """times: ساعات النشر اليومية (بتوقيت settings.PUBLISH_TIMEZONE). تُوزَّع المنشورات عليها بالترتيب يوماً بعد يوم،
    وتُتخطى المواعيد المحجوزة لمنشورات غير منشورة لنفس المصنع (فالدفعة الجديدة تلحق بآخر الطابور).
    بلا times: لا موعد، فيلتقطها المؤقت فور إنشائها."""
    factory = db.get(models.Factory, factory_id)
    if not factory:
        raise NotFoundError("المصنع غير موجود")

    requested_channels = list(dict.fromkeys(channel_ids))
    channels = {
        channel.id: channel
        for channel in db.scalars(select(models.SocialChannel).where(
            models.SocialChannel.id.in_(requested_channels)
        ))
    }
    missing_channels = [channel_id for channel_id in requested_channels if channel_id not in channels]
    if missing_channels:
        raise NotFoundError(f"قنوات غير موجودة: {', '.join(map(str, missing_channels))}")
    foreign_channels = [
        channel_id for channel_id in requested_channels if channels[channel_id].factory_id != factory_id
    ]
    if foreign_channels:
        raise InvalidInputError(f"قنوات لا تتبع المصنع: {', '.join(map(str, foreign_channels))}")
    inactive_channels = [channel_id for channel_id in requested_channels if not channels[channel_id].is_active]
    if inactive_channels:
        raise InvalidInputError(f"قنوات معطّلة: {', '.join(map(str, inactive_channels))}")

    query = select(models.Product).where(models.Product.factory_id == factory_id)
    if skus is not None:
        query = query.where(models.Product.sku.in_(skus))
    if category_codes is not None:
        query = query.where(models.Product.category_code.in_(category_codes))
    products = list(db.scalars(query.order_by(models.Product.id)))

    if skus is not None:
        found = {product.sku for product in products}
        missing_skus = [sku for sku in skus if sku not in found]
        if missing_skus:
            raise NotFoundError(f"منتجات غير موجودة لهذا المصنع: {', '.join(missing_skus)}")

    now = utc_now()
    slots: List[Optional[datetime]] = [None] * len(products)
    if times and products:
        occupied = db.scalars(select(models.Post.scheduled_at).where(
            models.Post.factory_id == factory_id,
            models.Post.scheduled_at.is_not(None),
            models.Post.status.in_((PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.APPROVED, PostStatus.PUBLISHING)),
        ))
        slots = generate_slots(
            len(products), times, now_utc=now, tz=ZoneInfo(settings.PUBLISH_TIMEZONE),
            start_date=start_date, occupied=set(occupied),
        )
    posts = []
    for product, slot in zip(products, slots):
        captions = {channel.id: build_caption(product, channel.platform) for channel in channels.values()}
        post = models.Post(
            factory_id=factory_id,
            sku=product.sku,
            media_type="image",
            media_urls=[product.primary_media_url],
            description=next(iter(captions.values())),
            status=PostStatus.APPROVED,
            approval=PostApproval.APPROVED,
            approved_at=now,
            scheduled_at=slot,
        )
        post.deliveries = [
            models.PostDelivery(
                channel=channels[channel_id],
                caption=captions[channel_id],
                status=DeliveryStatus.PENDING.value,
                attempts=0,
            )
            for channel_id in requested_channels
        ]
        db.add(post)
        posts.append(post)

    db.commit()
    for post in posts:
        db.refresh(post)
    return posts