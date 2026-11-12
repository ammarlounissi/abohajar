"""وسوم المصنع المفضّلة التي تُلحق بنص منشوراته.

- factories.hashtags: قائمة JSON مثل ["#مخبزة", "#وهران"] (فارغ = بلا وسوم).
إعدادات الصفحة تتبع المصنع (والمصنع يتبع الصفحة)، لا المستخدم؛ ورقم واتساب هو factories.phone_number الموجود."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d7e9f1c2b4"
down_revision: Union[str, Sequence[str], None] = "c8e2f4a6b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("factories") as batch:
        batch.add_column(sa.Column("hashtags", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("factories") as batch:
        batch.drop_column("hashtags")
