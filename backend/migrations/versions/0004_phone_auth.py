"""Phone identity, durable OTP quotas and opaque sessions; own membership discovery."""
from alembic import op
import sqlalchemy as sa

revision = "0004_phone_auth"
down_revision = "0003_source_provenance"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(320), nullable=True)
        batch.add_column(sa.Column("phone", sa.String(16), nullable=True))
        batch.create_unique_constraint("uq_users_phone", ["phone"])
    common = lambda: [sa.Column("id", sa.String(36), primary_key=True),
                      sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)]
    op.create_table("otp_challenges", *common(),
        sa.Column("phone", sa.String(16), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5"))
    op.create_index("ix_otp_phone", "otp_challenges", ["phone"])
    op.create_table("auth_budgets", *common(),
        sa.Column("key", sa.String(128), nullable=False, unique=True),
        sa.Column("events", sa.JSON(), nullable=False))
    op.create_table("auth_sessions", *common(),
        sa.Column("verifier", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False))
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    if op.get_context().dialect.name == "postgresql":
        op.execute("""CREATE POLICY membership_self_read ON memberships FOR SELECT
            USING (user_id = current_setting('app.user_id', true))""")


def downgrade():
    # Refuse before any DDL, including on the nontransactional SQLite test adapter.
    # Phone-only users cannot safely regain a NOT NULL email; no fabricated addresses.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM users WHERE email IS NULL")):
        raise RuntimeError("Cannot downgrade while phone-only accounts exist")
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP POLICY membership_self_read ON memberships")
    op.drop_table("auth_sessions")
    op.drop_table("auth_budgets")
    op.drop_table("otp_challenges")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_phone", type_="unique")
        batch.drop_column("phone")
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
