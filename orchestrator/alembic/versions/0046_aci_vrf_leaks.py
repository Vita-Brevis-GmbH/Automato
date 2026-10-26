"""ACI zwischen VRFs: Tenant der VRF je EPG, Route-Leaking je VRF (0.44.0)

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-23

- `epg.vrf_tenant`: ACI-Tenant der VRF aus dem aufgeloesten Ziel (`fvRsCtx.tDn`) - der APIC
  sucht einen VRF-Namen zuerst im eigenen Tenant, dann in `common`; bisher rieten wir das aus
  dem Inventar (vzAny, Wege zwischen VRFs).
- `inv_vrf.leaks`: Route-Leaking der VRF (`leakRoutes`: Praefix -> Ziel-VRFs).
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("epg", sa.Column("vrf_tenant", sa.String(100), nullable=True))
    op.add_column("inv_vrf", sa.Column("leaks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("inv_vrf", "leaks")
    op.drop_column("epg", "vrf_tenant")
