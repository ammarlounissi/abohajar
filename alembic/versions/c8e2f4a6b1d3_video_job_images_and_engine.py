"""مهام الفيديو: الصور المختارة بترتيبها، ومحرك الرندرة.

- factory_video_jobs.image_ids: معرّفات صور المكتبة كما ظهرت في معاينة الزبون (فارغ = اختيار عشوائي).
- factory_video_jobs.engine: remotion أو ffmpeg (فارغ = ffmpeg، كل المهام الموجودة)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2f4a6b1d3"
down_revision: Union[str, Sequence[str], None] = "b6d3f0a2c8e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("factory_video_jobs") as batch:
        batch.add_column(sa.Column("image_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("engine", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("factory_video_jobs") as batch:
        batch.drop_column("engine")
        batch.drop_column("image_ids")
