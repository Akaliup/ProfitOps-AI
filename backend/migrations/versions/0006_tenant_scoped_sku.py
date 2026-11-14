"""Scope Product SKU uniqueness by tenant."""
import sqlalchemy as sa
from alembic import op

revision = "0006_tenant_scoped_sku"
down_revision = "0005_weekly_chat"
branch_labels = depends_on = None

CONSTRAINT_NAME = "uq_products_tenant_sku"


def _sqlite_unique(columns, name):
    # PRAGMA is ignored inside an active SQLite transaction. Commit the prior
    # revision before rebuilding this referenced parent table.
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
        try:
            table = sa.Table("products", sa.MetaData(), autoload_with=bind)
            for constraint in list(table.constraints):
                if isinstance(constraint, sa.UniqueConstraint) and set(constraint.columns.keys()) in ({"sku"}, {"tenant_id", "sku"}):
                    table.constraints.remove(constraint)
            for index in list(table.indexes):
                if index.unique and set(index.columns.keys()) in ({"sku"}, {"tenant_id", "sku"}):
                    table.indexes.remove(index)
            table.append_constraint(sa.UniqueConstraint(*columns, name=name))
            with op.batch_alter_table("products", copy_from=table, recreate="always"):
                pass
            if bind.execute(sa.text("PRAGMA foreign_key_check")).first():
                raise RuntimeError("Foreign key violation after product migration")
        finally:
            bind.execute(sa.text("PRAGMA foreign_keys=ON"))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _sqlite_unique(["tenant_id", "sku"], CONSTRAINT_NAME)
    else:
        inspector = sa.inspect(bind)
        for uq in inspector.get_unique_constraints("products"):
            if uq.get("column_names") == ["sku"] and uq.get("name"):
                op.drop_constraint(uq["name"], "products", type_="unique")
        for idx in inspector.get_indexes("products"):
            if idx.get("column_names") == ["sku"] and idx.get("unique") and idx.get("name"):
                op.drop_index(idx["name"], table_name="products")
        if not any(uq.get("column_names") == ["tenant_id", "sku"] for uq in inspector.get_unique_constraints("products")):
            op.create_unique_constraint(CONSTRAINT_NAME, "products", ["tenant_id", "sku"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT sku FROM products GROUP BY sku HAVING COUNT(*) > 1")).first():
        raise RuntimeError("Cannot restore global SKU uniqueness: duplicate SKUs across tenants")
    if bind.dialect.name == "sqlite":
        _sqlite_unique(["sku"], "uq_products_sku")
    else:
        op.drop_constraint(CONSTRAINT_NAME, "products", type_="unique")
        op.create_unique_constraint("uq_products_sku", "products", ["sku"])
