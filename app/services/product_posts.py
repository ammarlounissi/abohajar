"""Create approved, timer-ready posts from catalog products."""
from datetime import datetime
from typing import List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.models import DeliveryStatus, PostApproval, PostStatus
from app.services.errors import InvalidInputError, NotFoundError
from app.services.post_templates import build_caption


def create_ready_posts(
    db: Session,
    factory_id: int,
    channel_ids: Sequence[int],
    *,
    skus: Optional[Sequence[str]] = None,
    category_codes: Optional[Sequence[str]] = None,
) -> List[models.Post]:
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

    now = datetime.utcnow()
    posts = []
    for product in products:
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