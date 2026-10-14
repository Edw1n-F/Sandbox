## 3. Drainage and Pluvial Flood: From Lumped SCS-CN Runoff to D8 Fill-and-Spill Routing with Priority-Flood Depressions and a Mass-Balance Assertion

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


## The question this chapter answers

A design-responsive drainage view must answer one question for the engineer who has to build it: *given a design edit, how much rain falls, how much is held, and how much is left with nowhere to go?* This chapter states precisely what the prototype computes today, surveys the published method families and their costs, recommends the one cheap in-stack upgrade that closes the biggest honest gap, and states what the result still cannot claim.

## 1. The current state, stated precisely

Today's drainage view is a three-stage pipeline, not a hydraulic model. **Generation** is SCS-CN runoff per cell, `S = 25400/CN − 254` mm and `Q = (P − 0.2S)²/(P + 0.8S)`, the standard curve-number method of USDA NRCS TR-55 ([TR-55 manual](https://www.hydrocad.net/pdf/TR-55%20Manual.pdf)). **Retention** is the ADR-015 partition: land cover sets how much runs off, explicit storage sets how much can be held, and no cell is counted in both — water and wetland generate nothing and instead draw a `STORAGE_DEPTH_M` allowance (1.50 m, 0.60 m), plus a 0.02 m depression allowance elsewhere. **Allocation** is the part that matters here: the leftover volume is placed by a **level-pool solve** — `_fill_to_volume` bisects for a single water-surface elevation `W` such that `Σ max(0, W − eᵢ)·A` equals the unstored volume — and each cell receives `max(0, W − eᵢ)`. It is a horizontal water plane over the basin, and it is **not routed**.

The prototype's own metadata says so plainly: the runoff volume "is still allocated by solving for a water level rather than routed downhill cell by cell. It is a screening view, not a flood prediction" (`backend/scripts/make_wetgrid.py`, `warning` field of the PNG sidecar). The served headline is therefore a volume, not a map: the **buffer deficit** of **3,170,180 m³**, defined as today's unstored runoff — water with nowhere to go (`backend/app/services/sandbox_model.py`, `ledger_for_scenario`).

Critically, the ingredients for routing **already exist and are unused**. `scripts/hydrology.py` implements Priority-Flood depression filling ([Barnes et al. 2014](https://www.sciencedirect.com/science/article/pii/S0098300413001337)), D8 flow direction ([O'Callaghan & Mark 1984](https://www.sciencedirect.com/science/article/pii/S0734189X84800110)) and `flow_accumulation`. On the 5 m analysis grid (664 × 474 cells, ≈786.55 ha), **10.3% of cells are closed pits** that Priority-Flood raises (≈32,500 of 314,736 cells), and flow accumulation is saved as `flow_accum_hsk.npy` — but the accumulated flow is **never used to move the runoff volume**. The upgrade below is therefore mostly *wiring what is already there*.

## 2. The method families and their cost

**(a) SCS-CN.** Cheap, standard, deterministic — but a *lumped* parameter, and the peer-reviewed literature is explicit that it is not physically based and is sensitive to soil group and antecedent moisture ([A Critical Review of the Soil Conservation Services – Curve Number Method in Hydrological Modelling, *Wetlands* 44, 2024](https://link.springer.com/article/10.1007/s13157-024-01873-w)). The prototype already answers this honestly with a `--sensitivity` CN band (±20%) rather than defending a single value.

**(b) Terrain-based.** **HAND** (Height Above Nearest Drainage) normalises elevation against the nearest drainage cell and is fast enough to map inundation directly ([Nobre et al. 2011](https://www.sciencedirect.com/science/article/pii/S0022169411002599); [NOAA HAND factsheet](https://www.weather.gov/media/owp/operations/HAND_factsheet.pdf)), but has documented limits: no bathymetry, levee handling, catchment isolation at high stage, and it is non-dynamic. **D8** routes all flow to the steepest neighbour; **D-infinity** distributes it across a facet ([Tarboton 1997](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/96WR03137)). **Priority-Flood** fills every depression to its lowest spill point in O(n log n).

**(c) Full 2D shallow-water.** LISFLOOD-FP ([user manual](https://www.bristol.ac.uk/media-library/sites/geography/migrated/documents/lisflood-manual-v5.9.6.pdf)), cellular-automata CA-ffé ([Jamali et al. 2019](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2018WR023679)) and TELEMAC are the real target — they solve *where, how deep, when*. CA-ffé is fast for a CA scheme but still needs a solver loop; all three are far past numpy-in-seconds.

**(d) Retention / SuDS.** Blue-green infrastructure ([HK DSD](https://www.dsd.gov.hk/EN/Publicity_and_Publications/Publicity/DSD_Sustainability_Reports/20/blue-green_drainage_infrastructure.html)), sponge-city programmes ([Nature Rev. Earth & Env., 2021](https://www.nature.com/articles/s43017-021-00251-y)) and InVEST's Urban Flood Risk Mitigation model ([InVEST UFRM](https://storage.googleapis.com/releases.naturalcapitalproject.org/invest-userguide/latest/en/urban_flood_mitigation.html)) all reduce runoff through per-pixel CN reduction — exactly the arithmetic the ledger already performs.

## 3. The recommended cheap upgrade — fill-and-spill routing

Turn the **existing D8 receiver and accumulation** into a **fill-and-spill (depression-cascade) routing** pass, an established technique for moving water through complex terrain ([Fill–Spill–Merge, Barnes et al. 2021](https://esurf.copernicus.org/articles/9/105/2021/)). Concretely:

- **What already exists:** the Priority-Flood conditioned surface, the D8 `receiver` array, `flow_accumulation`, the land-class grid, and `runoff_m`/`unstored_m` per cell.
- **What must be added:** (i) a **depression hierarchy** — label connected depressions on the filled surface and record each one's spill elevation; (ii) a **cascade pass** that walks cells in decreasing elevation order, carrying each cell's runoff to its receiver, filling a depression until it reaches its spill level and then passing the excess to the next depression downstream; (iii) a **mass-balance assertion** (`runoff in = stored + discharged`, to tolerance) as a first-class runtime check and unit test; (iv) **HAND** computed independently as a visual cross-check.

The result is a *routed* depth field where water arrives at a basin rather than being solved for globally — but it is still **screening**, and the buffer-deficit metric remains the headline.

## 4. Design responsiveness

Each lever in `INTERVENTION_EFFECTS` maps to a term the ledger recomputes:

- **Permeable paving** → CN override to **72** (an assumed mid-value for permeable pavement on HSG D, *not* a TR-55 lookup); it lowers the runoff coefficient without changing the land class.
- **Retention basins / flood-storage lakes** (`water_reservoir`, `flood_storage`) → convert cells to `water`: zero generation, 1.50 m storage.
- **Green space** (`green_space`, `urban_green`) → `green` (CN 80).
- **Drainage alignment** → reports **zero grid effect**, and correctly so: whether a network helps depends on pipe capacity and outfall levels this model does not have. Changing that would require a conveyance network with capacity and outfall elevations — a hydraulic model, not a wiring change.
- **Massing height** → no land-cover effect; it moves shade and exposure only.

## 5. Honesty and validation

The boundary is between a **screening ledger** — *does this design change the volume of runoff that has nowhere to go?* — and a **hydraulic model** — *where, how deep, when*. A routed cascade is still screening: no pipes, culverts, outfalls, backflow or 2D dynamics. Validation is by **monotonicity** (pave more → worse) and **mass balance**, not against recorded flood extents.

## 6. What to build next (dependency order)

1. Return `receiver` and `filled` from `condition()` and save the receiver as `.npy`.
2. Build the depression hierarchy (labels + spill elevations) on the filled surface.
3. Implement the fill-and-spill cascade over the D8 receiver; emit a routed depth field.
4. Add the mass-balance assertion as a runtime check and a unit test.
5. Compute HAND and render it as an independent cross-check overlay.
6. Wire `drainage_alignment` only once pipe/outfall capacity exists — until then, keep reporting zero.

## Summary

The prototype already generates runoff with the standard method, partitions retention honestly, and has the terrain algorithms for routing. The one gap worth closing is that the accumulated flow is computed but unused. Routing it as a fill-and-spill cascade, guarded by a mass-balance assertion and cross-checked by HAND, is a small, numpy-only, seconds-on-a-laptop change that turns a global level-pool solve into a defensible routed cascade — while remaining, explicitly, a screening tool and not a hydraulic model.

---
## 修改说明（current_round = 1/3）

### 已解决的必须修改项

1. **[必须修改 1：坑洼比例与元数据不一致]** 原文 §1 称 "11.5% of cells are closed pits … (≈32,500 cells)"。但已提交的 sidecar `wetgrid_hsk.json` 记录 `cellsRaised: 32500`、`cellsTotal: 314736`、`raisedFraction: 0.1033`，即 32,500 / 314,736 = **10.3%**（11.5% 会得出 ≈36,195，与 32,500 矛盾）。该 11.5% 来自 `make_wetgrid.py` 约第 114 行的**陈旧注释**，与脚本实际写出的元数据不符。**修改动作**：将该句中的 "11.5%" 替换为 **"10.3%"**，并改写为 "≈32,500 of 314,736 cells"，使百分比与细胞数一致。以已提交的 sidecar 为准。

2. **[必须修改 2：命名了错误的网格]** 同一句把这些被抬升的细胞说成在 "the 90 m extract" 上。实际上它们位于 **5 m 分析网格（664 × 474 cells，≈786.55 ha）**；90 m 只是源 DEM 的像元尺寸。**修改动作**：改写为 "On the 5 m analysis grid (664 × 474 cells, ≈786.55 ha)"，使读者不会再误以为洼地是在 90 m 分辨率下统计的。（校验：664 × 474 = 314,736 = sidecar 的 `cellsTotal`。）

### 已采纳的建议项

- **[建议 3：章节标题层级]** 按房格样式（章 = `##`，小节 = `###`），已将章标题由 `#` 改为 `##`。
- **[建议 4：补全 SCS-CN 综述引文标题]** 已将 "A critical review of the SCS Curve Number method, 2024" 补全为已发表标题 *"A Critical Review of the Soil Conservation Services – Curve Number Method in Hydrological Modelling"*（*Wetlands* 44, 2024），URL 保持为原真实链接 `https://link.springer.com/article/10.1007/s13157-024-01873-w`（未新增/编造任何来源）。
- **[建议 5：给出网格尺寸以核验比例]** 已按要求在陈述坑洼比例处一并给出网格尺寸 **664 × 474 @ 5 m，≈786.55 ha**，读者可据此核验比例。

### 未完全采纳的建议项（含原因）

- 无。五条意见（2 条必须修改 + 3 条建议）均已处理。

### 稳定性声明

除上述被审稿人明确指出的位置外，其余段落（§2–§6 及 Summary 的正文、所有既有引用与 URL）均**原封保留**，未作改动。
