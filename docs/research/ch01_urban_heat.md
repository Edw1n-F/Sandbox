# 1. Urban Heat: From a Hand-Authored Land-Class Index to a Physically-Grounded Sky-View-Factor and Shadow Screen Anchored to Hong Kong Landsat LST

> **⚠️ PARTLY SUPERSEDED — measured on the retired 786 ha extent. Read this first.**
>
> This chapter was written before the 14 Oct 2026 real-data swap, when the study
> area was the original ≈786 ha window (664 × 474 cells). Several figures below
> describe that extent and the sources it used, and **they no longer match what
> ships**. The literature, the method and the citations are unchanged and are the
> reason this chapter is kept.
>
> What changed, and where the current figure lives:
>
> | This chapter says | What ships now | Home |
> |---|---|---|
> | ≈786 ha / 664 × 474 cells | **43.5 ha / 117 × 148 cells** | `docs/DATA_SOURCES.md` §5 |
> | buffer deficit **3,170,180 m³** | **180,566.3 m³** | `STATUS.md` |
> | heat = a **relative 0–1 index, not a temperature** | a **°C surface-temperature field anchored to observed Landsat LST** | `docs/DATA_SOURCES.md` §4 |
> | DEM **~90 m** (SRTM, Open-Meteo) | **5 m Open3Dhk/LandsD tiles** | `docs/DATA_SOURCES.md` §1 |
> | **2,639 procedural** building envelopes | **396 measured** Open3Dhk + Overture footprints | `docs/DATA_SOURCES.md` §3 |
> | **10.3%** of cells closed pits (786 ha grid) | **11.7%** (43.5 ha window) | `README.md` |
>
> Treat every number above as history. When this chapter and `STATUS.md` disagree,
> `STATUS.md` is right — it is the sole live statement of what works.


## 1.1 What the prototype does today, stated precisely

The heat overlay that ships today is a *relative heat-exposure index*, not a temperature. `make_heatmap.py` maps each 5 m land-class cell through a hand-authored lookup table — `paved 1.00, bare 0.85, green 0.40, wetland 0.20, water 0.05` (`HEAT_INDEX` in `_overlay_common.py`) — and paints the result through a cool-to-hot colour ramp to an RGBA PNG. The file's own docstring is unambiguous: the output is "a *relative* screening index on a 0-1 scale — **not** a temperature in degrees, and not a modelled air-temperature field." Its JSON sidecar carries the machine-readable status `RELATIVE_INDEX_NOT_A_TEMPERATURE` and a warning that the figure must not be presented as degrees Celsius.

Two things follow. First, the index is honest about its provenance: `heatIndexSource` reads "Hand-authored screening index; awaiting verification." Second, it is a **surface** proxy only — it describes what the ground is made of, and nothing about the sun, the sky, the wind, or the air above it. The prototype already holds a shade/exposure hint per building (`BUILDING_SHADE_M_PER_M = 0.35`, `BUILDING_EXPOSURE_M2_PER_M = 1.8` in `sandbox_model.py`), but these are linear-in-height placeholders, labelled as "the crudest defensible assumption available," and they never touch the rendered heat field. The gap this chapter closes is therefore specific: **turn a static land-class tint into a design-responsive screen that still refuses to lie.**

## 1.2 The method families, and what each one costs

Three families generate urban-heat maps, and they sit on a sharp cost/defensibility trade-off.

**Satellite land-surface temperature (LST).** Landsat 8/9's Thermal Infrared Sensor delivers two thermal bands at 100 m native resolution, resampled to 30 m ([NASA TIRS](https://science.nasa.gov/mission/landsat/tirs/); [ESA TIRS product description](https://earth.esa.int/eogateway/instruments/tirs/products-information)). MODIS MOD11 provides a daily 1 km LST and emissivity product ([NASA MODIS MOD11](https://modis.gsfc.nasa.gov/data/dataprod/mod11.php)); ECOSTRESS, on the ISS, returns ~70 m thermal data at irregular overpasses and also yields evapotranspiration ([NASA ECOSTRESS](https://ecostress.jpl.nasa.gov/)). These give *real kelvin* — but they are snapshots, cloud-limited, coarse, and, decisively, **cannot respond to a design edit**. A planner who raises a block or plants a grove cannot re-fly a satellite. LST is also a *skin* temperature, not air temperature and not felt heat.

**Physically-based microclimate.** SOLWEIG computes mean radiant temperature (Tmrt) and comfort indices (UTCI, PET) from a building-height raster plus meteorological forcing, via sky-view-factor, shadow and short/long-wave radiation modules ([Lindberg et al., 2008](https://link.springer.com/article/10.1007/s00484-008-0162-7); [SOLWEIG / UMEP](https://umep-dev.github.io/solweig/)). It is raster-based, open source, and *is* design-responsive — but it needs a digital surface model and surface temperatures, and its full radiation chain is far heavier than numpy-in-seconds. ENVI-met is the neighbourhood gold standard: full 3D CFD coupling flow, plants and radiation, at hours per run ([ENVI-met](https://envi-met.com/)). Both are correct and both are out of scope for a laptop sandbox served in seconds.

**Proxy indices.** Local Climate Zones ([Stewart & Oke, 2012](https://journals.ametsoc.org/view/journals/bams/93/12/bams-d-11-00019.1.xml); [WUDAPT](https://www.wudapt.org/lcz/)) and land-cover heat-exposure indices are cheap, deterministic, and exactly what a raster sandbox can already do — but they are relative, and the prototype's hand-authored table is the crudest member of this family.

## 1.3 The recommended cheap upgrade: a sky-view-factor and shadow screen, anchored

The honest step up from a land-class tint is to make the field respond to **solar geometry and massing**, both computable in numpy on the existing 5 m grid, and then to **anchor** the relative scale so it is defensible rather than arbitrary.

**Sky-view factor (SVF).** SVF — the fraction of the sky hemisphere visible from a point — is the standard coupling between urban form and radiation, computable directly from a height raster ([Zakšek et al., 2011](https://www.mdpi.com/2072-4292/3/2/398/notes)). On the 5 m grid it is a horizon search over the building-height raster: for each cell, the maximum elevation angle to the surrounding massing over N azimuth bins; SVF falls as the surroundings rise. The prototype already owns the input — `citymodel_hsk.json` holds **2,639 massing envelopes** with heights from 10.9 m to 131.1 m (mean 35.3 m), rasterisable onto the same grid the heat overlay uses.

**Solar shadow.** A second numpy pass computes, for a given latitude (~22.43°N for HSK/HT), longitude (~113.99°E), date and time, the solar azimuth and altitude, then casts the massing as a shadow mask along the sun vector. Shaded cells are cooler; sunlit cells hotter. This is deterministic, runs in seconds, and — crucially — **moves when the massing moves**.

**Combining and anchoring.** The screen blends the existing land-class index (which already encodes albedo/evapotranspiration differences) with the SVF and shadow terms, so both greening and massing visibly move the field. The relative result is then **anchored to a published value**: a real Landsat 8/9 LST scene over HSK, or the HKO 1991–2020 climatological normals ([HKO Climatological Normals](https://www.hko.gov.hk/en/cis/normal.htm)), so the 0–1 scale maps to a stated, cited reference range instead of an invented one. The mapping is a calibration overlay, clearly labelled — the underlying index stays relative.

## 1.4 Design responsiveness — the core requirement

The whole point of a *sandbox* is that a design edit changes the map. The prototype's `INTERVENTION_EFFECTS` table already defines the levers; the SVF/shadow screen makes each one visible in heat:

- **Greening** (`green_space`, `urban_green` → `green` class) lowers the land-class term *and* raises effective shade/albedo, so cells cool twice over.
- **Water** (`water_reservoir`, `flood_storage` → `water` class) drops to the coolest index term (0.05).
- **Permeable paving** (`permeable_paving`, modelled as a Curve-Number override to CN 72) leaves the surface visually paved but should reduce its heat term — a natural place to attach a lower-albedo/pale-surface assumption, stated explicitly.
- **Massing** (`massing_height`) currently has *no* grid effect, by design, "it changes shade and exposure, not land cover." The SVF/shadow screen is exactly where that missing effect lands: raising or re-shaping massing lowers SVF and extends shadow, cooling the cells it shelters.

This is the design-responsiveness the user asked for, delivered inside the existing grids and the existing intervention vocabulary.

## 1.5 Honesty and validation

What this screen may claim: *"this cell is hotter/cooler than that surface, all else equal"* — a **relative** statement about form and cover. What it may **not** claim: a °C air temperature, or any human-comfort index. Those require the coupled radiation + wind + humidity chain of the SOLWEIG/ENVI-met class, which this prototype does not run. Validation is therefore by **anchoring**, not by prediction: calibrate the relative index against an observed LST scene and/or HKO air-temperature normals, report the result as a relative index, and label it screening-level at every surface (map legend, JSON sidecar, API response). The prototype's existing disclaimer pattern is the model to copy.

## 1.6 What to build next (dependency order)

1. Rasterise `citymodel_hsk.json` massing onto the 5 m grid as a height array.
2. Add a solar-position function (azimuth/altitude from lat/lon/date/time).
3. Compute the shadow mask; compute SVF by horizon search.
4. Blend land-class index + SVF + shadow into one relative field; keep the ramp and disclaimer.
5. Add an anchoring layer against a published LST/HKO normal; store the calibration in the sidecar.
6. Wire `massing_height` (and greening/water) edits through the SVF/shadow recompute so the field moves per design edit.

### Summary

Today's heatmap is an honest but inert land-class tint. The cheapest defensible upgrade that makes it *design-responsive* is a numpy SVF + solar-shadow screen over the existing 2,639-envelope massing, blended with the land-class index and anchored to a published Hong Kong LST or air-temperature normal. It stays inside pure numpy-in-seconds, and it still cannot claim a °C temperature — and it says so.
