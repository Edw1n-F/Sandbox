# Data sources

Every input, where it came from, what licence it carries, and — importantly —
**what was tried and rejected.** The rejected list is the useful half: it stops
the next person spending an afternoon on a source that returns plausible
nonsense.

The generated artefacts (`dem_hsk.json`, `landcover_hsk.json`,
`rasters/*.json`) carry the machine-readable version of all of this. This
document is the human-readable narrative.

---

## Summary

| Layer | Source | Licence | Resolution | Key? | Status |
|---|---|---|---|---|---|
| Elevation | Open-Meteo elevation API | open data | **~90 m** (measured) | No | **In use** |
| Land cover | ESA WorldCover 10 m v200 (2021) | CC BY 4.0 | 10 m | No | **In use** |
| Base imagery | OpenStreetMap standard tiles | ODbL | — | No | **In use** |
| Study-area extent | OSM place anchors via Nominatim | ODbL | — | No | **In use, provisional** |
| Terrain rendering | mapbox terrain-RGB encoding | open spec | — | — | **In use** |

A full refresh is **under 1 MB** of API calls plus two range-read windows of a
public GeoTIFF. No accounts, no keys, no large files.

---

## 1. Elevation — Open-Meteo elevation API

- **Endpoint:** `https://api.open-meteo.com/v1/elevation`
- **Upstream models:** a downsampled global elevation model, **~90 m** posting
  (measured on this extract — see "Resolution" below)
- **Auth:** none
- **Fetcher:** `backend/scripts/fetch_dem.py`
- **Outputs:** `dem_hsk.npy` (float32 metres), `dem_terrain_rgb.png`, `dem_hsk.json`

### Why this source

The Lands Dept 5 m DTM was ruled out early: ~290 MB, needs explicit download
permission, and it is **not bare-earth** — it carries elevated roads and depicts
vegetation at vegetation height, so using it naively would bias any volume figure
in exactly the quantity being computed.

### Control points — the part that matters

The fetcher asserts two known values **before it will write anything**:

| Control | Expected | Observed |
|---|---|---|
| Everest summit (27.9881 N, 86.9250 E) | 8,000–9,500 m | 8,724 m |
| South Atlantic (−20.0, −30.0) | ±50 m | 0.0 m |

A response failing either aborts the run. This exists because of what happened
with the source below.

### Rejected: AWS `elevation-tiles-prod` (terrarium)

**This source is a trap and is documented so nobody retries it.**

It returns HTTP 200 with 256×256 PNGs that look like plausible elevation tiles.
The pixels are **not** terrarium-encoded. Decoding
`(R·65536 + G·256 + B)·0.1 − 32768` gives:

| Control | Expected | Decoded |
|---|---|---|
| Everest | 8,848 m | ~1,000,000 m |
| South Atlantic | 0 m | 792,940 m |
| Sierra Nevada | ~3,000 m | 818,976 m |
| HSK/HT | ~0–200 m | 807,633 m |

Both the `terrarium` **and** `normal` path prefixes fail identically. The red
channel sits pinned near 128 regardless of location, which is the tell.

Had this not been checked it would have produced a smooth, plausible,
completely wrong landscape — the worst possible failure, because nothing would
have looked broken.

### Resolution — measured, not assumed

**The effective resolution is ~90 m, not the 1 arc-second (~30 m) that was
originally assumed here.**

The DEM was requested on a 1 arc-second lattice, which is the textbook answer
for SRTM. The API does not serve SRTM at 1 arc-second: it serves a downsampled
global model. The result was a **78 × 110** array containing only **28 × 37
distinct samples** — every source row and column repeated about three times.

Two things followed from measuring instead of assuming:

1. **The oversampling was removed.** Deduplicating repeated rows and columns is
   lossless by construction — a cell is only dropped when it is byte-equal to its
   predecessor — and it preserves all 84 distinct elevations exactly. The shipped
   grid is 28 × 37, i.e. ~85 m E–W × ~90 m N–S.
2. **Every resolution claim in this repository was corrected.** The earlier text
   said "1 arc-second (~30 m)" in four places. There is no 30 m data here, and
   re-sampling on a finer lattice would only duplicate the same values again.

The general lesson: **an API's advertised resolution is not the resolution you
get.** Count the distinct samples.

> Note the contrast with the AWS bucket, which failed loudly under the same kind
> of check. Here the data was *correct* but the metadata around it was wrong —
> a quieter failure, and one that only a measurement would catch.

### Rate limiting

The API accepts **100 coordinate pairs per request** (more returns HTTP 400 with
a helpful message) and throttles on request *frequency*. The fetcher therefore:

- batches 100 coords per request,
- sleeps 2 s between batches (a full grid takes ~3 minutes),
- honours `Retry-After`, else exponential backoff with jitter, up to 10 attempts,
- **caches every batch to `data/dem_cache/`**, keyed on the coordinates.

That last point matters most: an interrupted run resumes for free, and changing
the resolution misses the cache rather than serving the wrong grid.

---

## 2. Land cover — ESA WorldCover 10 m v200

- **Tiles:** `N21E111`, `N21E114` (3°×3°, named by south-west corner)
- **URL pattern:**
  `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif`
- **Licence:** CC BY 4.0 (ESA) — attribution required
- **Fetcher:** `backend/scripts/fetch_landcover.py`
- **Output:** `landclass_hsk.npy`, `landcover_hsk.json`

Read with a **windowed COG read** via `rasterio`, so only the ~1.5 % of each
3°×3° tile that the study area occupies is transferred. Resampled onto the 5 m
analysis grid with **nearest neighbour** — class codes must never be
interpolated.

### Why WorldCover and not OpenStreetMap

OSM land cover is crowd-sourced and patchy at 10 m in Hong Kong. WorldCover is
a consistent, modelled, per-pixel product with a documented class scheme. For
scenario comparison that consistency is the point: **both scenarios must see
exactly the same ground**, or the comparison is meaningless.

### The tile-boundary bug

The extent is 113.982–114.005 °E. The tile boundary is at exactly **114 °E**.

The first implementation read only the tile containing the south-west corner, so
the eastern 21 % of the extent came back as **nodata** — 67,064 cells — which
were then filled with the default class. That silently fabricated ~167 ha of
"paved" ground, and made the runoff figure wrong because of it.

`covering_tiles()` now enumerates every intersecting tile and composites them.
The failure mode is recorded in its docstring, because "nodata quietly becomes
the default class" is the kind of bug that survives review.

### Class mapping — 11 ESA classes to 5

| ESA code | Class | → Ours |
|---|---|---|
| 10 | Tree cover | green |
| 20 | Shrubland | green |
| 30 | Grassland | green |
| 40 | Cropland | paved ⚠️ |
| 50 | Built-up | paved |
| 60 | Bare / sparse vegetation | bare |
| 70 | Water bodies | water |
| 80 | Snow and ice | water |
| 90 | Herbaceous wetland | wetland |
| 95 | Mangroves | wetland |
| 100 | Moss and lichen | green |

Collapsing 11 → 5 is deliberate: the wet grid needs to know what **sheds water**
and what **holds it**, not what the crop is.

⚠️ **Cropland and built-up are not the same hydrologically.** Both map to
`paved`. HSK/HT is post-reclamation, so cropland there is mostly formed land
awaiting development — but this is a compromise and it is recorded in the output
metadata rather than hidden.

Observed result: **67.4 % paved, 30.6 % green, 1.6 % bare, 0.4 % water** —
consistent with a dense new town. The raster visibly resolves individual blocks,
the NE–SW drainage channel, and quarry/construction patches.

### Caveats

- Single 2021 epoch — **no change since then**, a real gap for an area under
  construction.
- 10 m source on a 5 m grid adds no detail below 10 m. The 5 m land class is
  really 10 m.
- ESA codes are modelled classifications, not ground truth.

---

## 3. Study-area extent — OSM anchors, provisional

- **Extent:** 113.982–114.005 °E, 22.425–22.455 °N (≈786 ha)
- **Derivation:** machine-retrieved OSM place anchors, recorded with queries and
  OSM ids in `backend/data/scene_hsk.json`
- **Status:** `PROVISIONAL_OPERATIONAL_EXTENT`

**This is not the gazetted NDA boundary.** It is a rectangular operational extent
chosen so the sandbox had a stable, citable rectangle on a fixed day.

> **Replace it with the sheet extent of OZP S/HSK/3.** Every raster,
> land-class grid and validation target shifts if it changes — which is exactly
> why it lives in a data file rather than being typed into a script.

### Why HSK/HT and not Deep Bay

The 27-task project plan anchors on Deep Bay (San Tin / Tai Sang Wai / Mai Po)
while the hackathon brief carries HSK/HT. This conflict was unresolved for
weeks. **HSK/HT was chosen on 6 Oct 2026** on the strength of its public
evidence base: approved EIA, OZP S/HSK/3, and a LegCo study.

---

## 4. Terrain rendering — mapbox terrain-RGB

Elevation has to reach Cesium somehow. Cesium's stock `CesiumTerrainProvider`
wants quantized-mesh from a tiling service; Cesium ion wants a token we
deliberately do not use.

So the DEM is encoded as **terrain-RGB** —
`metres = (R·65536 + G·256 + B)·0.1 − 32768` — a published open encoding, served
through Cesium's first-class `CustomHeightmapTerrainProvider`
(`frontend/src/view/demProvider.ts`). The raster is 2.4 × 3.3 km, so a single
image beats a tile pyramid. The whole study area is **1.6 KB** as a PNG.

---

## Vertical datum — unresolved

Hong Kong has two vertical datums: **mPD** (Principal Datum, ~1985) and **mCD**
(Chart Datum, mean sea level). They differ by **0.146 m**.

That figure is a named constant in `tests/test_constants.py`, with a test that
exists to be revisited when a tide series is added. There is **no tide series in
this prototype**, so no correction is applied. 0.146 m is half a 0.3 m LiDAR
cell — small, but enough to flip a marginal cell between wet and dry.

Related: elevation here is **orthometric (mPD)**, not ellipsoidal, and there is
**no bare-earth correction**. If vegetation or structures bias it upward, a
*deficit* estimate is biased in the same direction.

---

## Also tried, not adopted

| Source | Why not |
|---|---|
| **Lands Dept 5 m DTM** | ~290 MB, needs permission, **not bare-earth** |
| **ISRIC SoilGrids** (WCS) | Endpoint unreachable from this host (HTTP 000). Would have replaced the *assumed* hydrologic soil group with real soil data — the highest-value remaining upgrade |
| **OpenTopography** | Unreachable from this host (HTTP 000) |
| **HydroSHEDS** | Reachable. Drainage networks would enable real flow routing, but it is a large download and the study area is small enough that routing adds little at 30 m |
| **Overpass (OSM)** | Repeatedly timed out. Nominatim worked, so the extent was derived from place anchors instead |
| **AWS elevation-tiles-prod** | Returns plausible, non-terrarium pixels. See §1 |

---

## Refreshing the data

```bash
cd backend
python scripts/fetch_landcover.py          # ~7 s
python scripts/fetch_dem.py               # ~3 min, self-paced, resumable
python scripts/make_wetgrid.py --rainfall 450
python scripts/make_heatmap.py
```

Each fetcher prints its own provenance, writes a `.json` manifest, and refuses
to write output that fails its sanity checks. Each registers its result against
the seeded scene, so the viewer's switches pick the new data up on reload.

To swap in a **better DEM** (a permitted 5 m DTM, or a bare-earth-corrected
surface), drop it in as `dem_hsk.npy` on the study-area bbox, north-up, float32
metres, and set `terrain_source` accordingly. Nothing else needs to change.
