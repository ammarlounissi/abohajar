"""مساحات الزبائن: كل صفحة مربوطة بخدمة لها مصنع خاص بها.

- factories.kind: supplier (الموجودون) أو customer.
- factories.phone_number يصير اختيارياً: مساحة الزبون لا رقم لها.
- customer_workspaces: (المستخدم، معرّف الصفحة) -> المصنع الذي يملك وسائطها."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6d3f0a2c8e1"
down_revision: Union[str, Sequence[str], None] = "a4c7e1d9b3f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("factories") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="supplier"))
        batch.alter_column("phone_number", existing_type=sa.String(length=20), nullable=True)

    op.create_table(
        "customer_workspaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("page_id", sa.String(length=100), nullable=False),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "page_id", name="uq_customer_workspaces_user_page"),
    )
    op.create_index("ix_customer_workspaces_user_id", "customer_workspaces", ["user_id"])
    op.create_index("ix_customer_workspaces_factory_id", "customer_workspaces", ["factory_id"])


def downgrade() -> None:
    """يفشل إن وُجدت مساحات زبائن (phone_number فارغ): احذفها أو انقلها أولاً، فلا نحذف بيانات الزبائن ضمناً."""
    with op.batch_alter_table("factories") as batch:
        batch.alter_column("phone_number", existing_type=sa.String(length=20), nullable=False)
        batch.drop_column("kind")
    op.drop_index("ix_customer_workspaces_factory_id", table_name="customer_workspaces")
    op.drop_index("ix_customer_workspaces_user_id", table_name="customer_workspaces")
    op.drop_table("customer_workspaces")
