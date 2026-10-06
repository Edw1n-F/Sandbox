"""Storage-layer row definitions for ``intervention`` (§4.2.3).

One normalised model serves both user-drawn and AI-proposed features, so both
travel the same validate -> store -> render pipeline.

ADR-030 lives here in the storage contract: ``status`` is a server-owned column.
A caller cannot assert ``status='validated'`` on create -- the service hard-codes
``draft`` and a separate validation step is required before the engine will
compute on the geometry.
"""

from typing import Any, NotRequired, TypedDict


class InterventionRow(TypedDict):
    """One row of the ``intervention`` table."""

    id: str
    plan_id: str  # REFERENCES plan(id) ON DELETE CASCADE
    geometry_type: str  # CHECK IN ('Point','LineString','Polygon')
    geometry: dict[str, Any]  # GEOMETRY NOT NULL, SRID 4326, standard GeoJSON
    intervention_type: str  # "透水铺装" | "绿地" | "蓄水池" | "道路" | ...
    source: str  # CHECK IN ('manual','ai')
    params: NotRequired[dict[str, Any]]  # {area, permeability, storageVolume, elevation, ...}
    style: NotRequired[dict[str, Any]]  # {color, opacity, extrusionHeight}
    status: str  # CHECK IN ('draft','validated') -- server-owned, see ADR-030
    created_at: str
    updated_at: str


INTERVENTION_COLUMNS: tuple[str, ...] = tuple(InterventionRow.__annotations__)
