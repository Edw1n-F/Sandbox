#!/usr/bin/env python
"""Build a 3D city model for the sandbox, and make it editable.

    python scripts/make_citymodel.py

## Where the buildings come from, and why not from OSM

The obvious source is OpenStreetMap building footprints, and that was tried
first. It is not reachable from this host:

- Overpass returned 504 from the main endpoint, 000 from four mirrors, and an
  empty result set from the one that answered (``overpass.osm.ch`` appears to
  hold a partial extract, not a global one).
- Microsoft's GlobalML Building Footprints is now gated: the public blob returns
  ``409 Public access is not permitted on this storage account``.
- A Geofabrik Hong Kong extract is ~90 MB and needs a PBF parser that will not
  build here either.

So the massing is **procedural, derived from real land cover**: the ESA
WorldCover 10 m ``paved`` class says where the ground *is* built up, and blocks
are placed only there. The land-use input is real; the individual building
footprints are not surveyed, and every feature says so.

## What is deliberately not claimed

These are massing envelopes for visual context, not buildings. No footprint is
surveyed, no height is measured, and ``height_source`` records that the heights
are a distribution over building classes rather than ``building:levels`` from
OSM. The moment real footprints become available, this script's output is the
thing that gets replaced -- and it is deliberately shaped like a GeoJSON
FeatureCollection so the substitution is a data swap, not a rewrite.

## Why generate them at all

Because the spec's scenario sandbox needs something to put interventions
*against*. A reservoir proposal is meaningless floating over grass, and the AI
module's whole value is that it can change the model. Buildings are what the
user then adjusts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
for extra in (BACKEND_DIR, BACKEND_DIR / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.config import settings  # noqa: E402
from _overlay_common import LAND_CLASSES, load_overlay  # noqa: E402

# Block size in analysis cells. 5 m cells, so a 6-cell block is a 30 m footprint
# and a 3-cell gap between blocks gives streets.
BLOCK_CELLS = 6
GAP_CELLS = 3
MIN_BLOCK_CELLS = 18  # ~450 m2, below this a paved patch is not a building

# Height distribution by distance from the district edge, in metres. New-town
# housing in HSK/HT is overwhelmingly 3-12 storeys; the tall tail is the
# occasional newer tower. These are *assumed* classes, not measured heights.
HEIGHT_CLASSES = (
    # (max storeys, share, label)
    (4, 0.42, "village_house"),
    (12, 0.40, "new_town_block"),
    (26, 0.15, "newer_tower"),
    (38, 0.03, "landmark_tower"),
)
STOREY_M = 3.1  # Hong Kong residential floor-to-floor, a common planning figure


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def pick_height(rng: np.random.Generator) -> tuple[float, str]:
    """Draw a building height and record which class it came from."""
    roll = rng.random()
    cumulative = 0.0
    for storeys, share, label in HEIGHT_CLASSES:
        cumulative += share
        if roll <= cumulative:
            # Jitter within the class so a block is not uniformly flat.
            jitter = rng.uniform(0.88, 1.12)
            return round(storeys * STOREY_M * jitter, 1), label
    storeys, _, label = HEIGHT_CLASSES[-1]
    return round(storeys * STOREY_M, 1), label


def build_blocks(paved: np.ndarray, cell_w_deg: float, cell_h_deg: float,
                 lon_min: float, lat_max: float, seed: int) -> list[dict]:
    """Tile the paved mask with separated blocks and give each one a height."""
    rng = _rng(seed)
    rows, cols = paved.shape
    pitch = BLOCK_CELLS + GAP_CELLS
    out: list[dict] = []

    for r0 in range(0, rows, pitch):
        for c0 in range(0, cols, pitch):
            r1 = min(r0 + BLOCK_CELLS, rows)
            c1 = min(c0 + BLOCK_CELLS, cols)
            patch = paved[r0:r1, c0:c1]
            if patch.size < MIN_BLOCK_CELLS:
                continue
            # Require the block to be mostly built up, so a single stray paved
            # cell does not become a tower in a field.
            if patch.mean() < 0.55:
                continue

            west = lon_min + c0 * cell_w_deg
            east = lon_min + c1 * cell_w_deg
            north = lat_max - r0 * cell_h_deg
            south = lat_max - r1 * cell_h_deg
            height, label = pick_height(rng)
            out.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [round(west, 7), round(south, 7)],
                        [round(east, 7), round(south, 7)],
                        [round(east, 7), round(north, 7)],
                        [round(west, 7), round(north, 7)],
                        [round(west, 7), round(south, 7)],
                    ]],
                },
                "properties": {
                    "id": f"bldg-{len(out):04d}",
                    "building_type": "massing_envelope",
                    "height_m": height,
                    "storeys": max(1, round(height / STOREY_M)),
                    "height_class": label,
                    "height_source": "ASSUMED_DISTRIBUTION_NOT_SURVEYED",
                    "footprint_source": "derived from ESA WorldCover paved class",
                    "provisional": True,
                },
            })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7,
                        help="fixed so the city model is reproducible")
    parser.add_argument("--out", default=None,
                        help="output GeoJSON (default data/rasters/citymodel_hsk.json)")
    args = parser.parse_args()

    ov = load_overlay()
    grid = ov.grid
    lon_min, lat_min, _, lat_max = ov.bounds
    import math

    cw = float(grid["cell_width_m"]) / (111_320.0 * math.cos(math.radians((lat_min + lat_max) / 2)))
    ch = float(grid["cell_height_m"]) / 110_574.0

    paved = ov.class_mask("paved")
    blocks = build_blocks(paved, cw, ch, lon_min, lat_max, args.seed)

    heights = np.array([b["properties"]["height_m"] for b in blocks]) if blocks else np.array([0.0])
    by_class: dict[str, int] = {}
    for b in blocks:
        k = b["properties"]["height_class"]
        by_class[k] = by_class.get(k, 0) + 1

    collection = {"type": "FeatureCollection", "features": blocks}
    out = Path(args.out) if args.out else settings.rasters_dir / "citymodel_hsk.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")

    # Provenance sidecar, read by GET /terrain and shown by the viewer. The
    # status string is deliberately blunt so nobody screenshots a skyline and
    # calls it measured.
    meta = {
        "status": "PROVISIONAL_MASSING_NOT_SURVEYED",
        "features": len(blocks),
        "heightM": {
            "min": round(float(heights.min()), 1) if blocks else 0.0,
            "max": round(float(heights.max()), 1) if blocks else 0.0,
            "mean": round(float(heights.mean()), 1) if blocks else 0.0,
        },
        "byClass": by_class,
        "footprintSource": "derived from ESA WorldCover 10 m paved class",
        "heightSource": "ASSUMED_DISTRIBUTION_NOT_SURVEYED",
        "whyNotOsm": (
            "Tried and unreachable from this host: Overpass returned 504 from the "
            "main endpoint and 000 from four mirrors, with an empty result from "
            "the one instance that answered; Microsoft GlobalML Building "
            "Footprints now returns 409 'Public access is not permitted on this "
            "storage account'; a Geofabrik HK extract is ~90 MB and its PBF "
            "parser will not build here either."
        ),
        "editable": True,
        "note": (
            "Massing envelopes for visual context, and something for the AI "
            "module to change. Not surveyed buildings. When real footprints "
            "become available this file is the thing that gets replaced, and it "
            "is deliberately a plain GeoJSON FeatureCollection so that is a data "
            "swap rather than a rewrite."
        ),
    }
    meta_path = settings.rasters_dir / "citymodel_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Register on the seeded scene so the viewer finds it.
    from app import db
    from app.services.scene_service import attach_overlay

    registered = 0
    for doc_path in db.list_docs(settings.scenes_dir):
        doc = db.read_doc(doc_path)
        if doc and doc.get("study_area"):
            attach_overlay(doc["id"], "city_model_path", "rasters/citymodel_hsk.json")
            registered += 1
    if registered:
        print(f"[city] registered on {registered} scene(s)")

    print(f"[city] {len(blocks)} massing envelopes from the ESA WorldCover paved class")
    print(f"[city] height  min={heights.min():.1f}  max={heights.max():.1f}  "
          f"mean={heights.mean():.1f} m")
    print(f"[city] classes {by_class}")
    print(f"[city] wrote {out} and citymodel_meta.json")
    print("[city] PROVISIONAL: footprints are procedural, heights are an assumed")
    print("       distribution. Not surveyed buildings -- see the module docstring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
