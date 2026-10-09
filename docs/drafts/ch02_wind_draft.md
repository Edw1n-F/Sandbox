## 2. Wind and Ventilation: From Axis-Aligned Frontal Area Density to Oriented Directional Frontal Area and Least-Cost-Path Corridors under a Comparative AVA Framing

### 2.1 What the prototype computes today — and what it admits it cannot

The sandbox's current wind layer is a **frontal area density (FAD)** screen. For each of the 16 cardinal wind directions it sums the building face presented perpendicular to the wind and divides by site area: `FAD(dir) = Σ (projected_width × height) / site_area` — a dimensionless *shape* indicator where low values mean wind can penetrate and high values mean the site is sheltered from that direction. It needs only footprints and heights (the 2,639 massing envelopes already on disk) and runs in milliseconds.

The module is candid about its ceiling. It states outright that "FAD is not a velocity ratio and the two are never interchanged," and its `limitation` field records the defect motivating this chapter: because "every footprint in this project is an axis-aligned rectangle, generated from the land-cover raster… the result is symmetric about north-south and east-west: NE, SE, SW and NW all score identically." That is exact, not approximate: for an axis-aligned box the projected width is `a·|sin φ| + b·|cos φ|`, an even function, so a **NE wind and a SW wind produce byte-identical FAD**. The published prevailing wind at Hung Shui Kiu is **NE/ENE/E, together 40.8% of the year** ([AVRG73 AVA, PlanD/CEDD](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf)) — so the screen is blind along precisely the axis that matters. The repo's test suite locks this in, asserting that "width is symmetric about the cardinal axes."

### 2.2 The governing method, and why comparative is not a compromise

Hong Kong's method is the **Air Ventilation Assessment (AVA)** under [Technical Circular No. 1/06 and its Annex A Technical Guide](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf). Its indicator is the **wind velocity ratio VR = Vp/V∞**: pedestrian wind at 2 m divided by wind at the top of the boundary layer, "typically assumed to be around 400 m to 600 m above city centre" (TC 1/06 §4; AVRG73 assumes ≈596 m for this site). Higher VR means *less* impact from a development. It has three escalating stages — **Expert Evaluation → Initial Study → Detailed Study** — and reports **Site (SVR)** and **Local (LVR)** spatial-average ratios (§30), with wind profiles from a boundary-layer wind tunnel recommended for the Detailed Study (§21) and reference data from the **HKO Waglan Island** record (§11, §14). Sixteen directions are required to work out VR (§19).

Two sentences decide this chapter's design. First, on tools: "**Computational Fluid Dynamics (CFD) may be used with caution**, it is more likely admissible for the Initial Studies. **There is no internationally recognized guideline or standard for using CFD in outdoor urban scale studies**" (§17). Second, decisively, on the criterion: "The framework developed at this stage **does not provide an absolute benchmark standard**… The framework would however, enable **comparison of design options** in external air ventilation terms and identification of potential problem areas" (§6). The AVA policy paper behind the circular agrees from the design side: "**A design having a higher velocity ratio would be considered as a better design than one having a lower velocity ratio**… whether the better design option meets a standard cannot be known" ([Ng & Cheng, 2009, *Building and Environment*](https://www.sciencedirect.com/science/article/pii/S0360132308001455)).

This is the licence for a screening tool. An absolute pass/fail would require a VR — i.e. CFD or a wind tunnel, which the constraints exclude. A **comparative** screen is exactly what the official framework asks for. So the honest target is not "compute a VR" but "**rank schemes and axes against each other, and never print a verdict**."

### 2.3 The FAD literature

Frontal area index/density (λf) has a long urban-climatology lineage, formalised as an aerodynamic-roughness parameter in [Grimmond & Oke (1999)](https://journals.ametsoc.org/view/journals/apme/38/9/1520-0450_1999_038_1262_apouad_2.0.co_2.xml) and reviewed comprehensively in [Frontal area index: a review of calculation methods (2022)](https://www.sciencedirect.com/science/article/pii/S0360132322008186), which catalogues vector and raster computation routes and their differing sensitivities. FAD is a legitimate roughness proxy that correlates with ventilation at the aggregate scale, but it is **geometric**: it carries no velocity, turbulence, flow separation or threshold. It can stand in for *directional exposure and blockage*; it cannot stand in for *flow*.

### 2.4 The recommended cheap upgrade — two concrete steps

**Step (a): rasterise the footprints and compute a true per-cell projected width using each footprint's real orientation.** Today the code reads no orientation because the massing model has none — every footprint is axis-aligned. The fix is to assign each footprint a true bearing (from its polygon's principal axis, or the street grain) and use the full oriented formula `a·|sin(θ−φ)| + b·|cos(θ−φ)|` instead of the axis-aligned shortcut. On a numpy grid this is a per-cell loop writing a height/width array then a direction-wise reduction — still milliseconds. This **breaks the NE≡SW symmetry** and lets a NE wind be distinguished from a SW one. It is the highest-value change and is purely arithmetic. A 2025 review of [raster-based FAD techniques](https://link.springer.com/chapter/10.1007/978-981-95-2169-2_37) confirms raster per-cell computation is the standard low-cost route.

**Step (b): build least-cost-path ventilation-corridor maps on an FAD (or inverse-FAD) cost surface.** The corridor literature is explicit that low-FAD / high-openness cells form a cost surface through which a Dijkstra / least-cost search traces the paths wind is most likely to follow; the classic Hong Kong demonstration is [Wong et al. (2010), GIS techniques for mapping urban ventilation using frontal area index and least-cost-path analysis](https://www.isprs.org/proceedings/XXXVIII/part2/Papers/78_Paper.pdf), and the method remains current in [IBPSA BS2023 corridor identification](https://publications.ibpsa.org/proceedings/bs/2023/papers/bs2023_1543.pdf), a 2026 [CFD–LCP framework](https://www.sciencedirect.com/science/article/pii/S2950509726001188) and a 2025 [ML corridor framework](https://www.mdpi.com/2220-9964/14/7/244). A cost surface is `cost = 1/(1+FAD)` (cheap where FAD is low); scipy's `sparse.csgraph` Dijkstra over the 4/8-neighbour grid returns the cheapest through-route, drawn as a corridor. **Inputs required:** the 5 m footprint raster (from `citymodel_hsk.json`), the height grid, per-footprint orientation, and the 16 direction bearings. **Outputs:** a per-direction corridor raster plus a baseline-vs-scenario FAD ratio.

**Present it relatively.** Under the §6 comparative framing, report `scenario_FAD / baseline_FAD` per direction rather than an absolute number — a ratio is a comparison, which the framework authorises, and it is robust to the FAD scale being uncalibrated.

### 2.5 Wind–heat coupling

A ventilation layer is part of the heat story because moving air is an **advective heat sink**: pedestrian-level breeze flushes warm air from the canopy and raises the convective heat-transfer coefficient at surfaces. The AVA policy paper treats this as the designer's second lever alongside shade — "design our outdoor environment to **maximize wind speed and minimize solar radiation** to achieve comfort" (Ng & Cheng, 2009). That is why the heat and wind chapters share one geometry: a taller, denser, podium-covered scheme simultaneously reduces sky-view cooling and blocks the breeze, and the two compound. The wind screen therefore feeds the heat screen a *ventilation-availability* term, not a temperature.

### 2.6 Design responsiveness

Each design lever maps onto a computable FAD/corridor change, tied to the prototype's `INTERVENTION_EFFECTS` vocabulary:

- **Massing height** — raising `height_m` increases every footprint's frontal area linearly, raising FAD; *varying* heights (not uniform) is published as better for downwash (Ng & Cheng, 2009).
- **Orientation / alignment** — the oriented projected width (Step a) makes rotation a first-class, directional effect.
- **Podium-free design and ground-level gaps** — AVRG73 lists "podium-free design, for wind penetration under the building" and "empty bays at ground-floor level" among its effective measures; a podium is a ground-level blockage, so removing it should *lower* ground FAD and open a corridor. This is the lever the current model cannot yet see.
- **Dedicated wind corridors** — directly the Step (b) output, aligned to the NE prevailing axis.
- **Greening / roughness** — vegetation raises surface roughness and slows near-ground flow; represented as a roughness penalty on corridor cost, not as an FAD change.

### 2.7 Honesty and validation

The boundary must be printed on every number. **FAD is not a velocity ratio**, and the two are never interchanged: FAD is dimensionless area-per-area, VR is a speed ratio; they differ by orders of magnitude and share no calibration. A VR still needs CFD or a wind tunnel. **No threshold is asserted** — TC 1/06 §6 provides none. What the screen *may* claim: that scheme A is less exposed than scheme B under the NE prevailing axis, and that a NE wind is distinguishable from a SW one. What it *may not*: that any scheme "passes" AVA, or that a computed FAD equals the published HSK SVR (0.28 baseline → 0.31 proposed). Every output carries the label **screening-level, comparative, not engineering-grade.**

### 2.8 What to build next (dependency order)

1. Per-footprint orientation assignment from polygon principal axis.
2. Rasterise footprints + heights to 5 m grids.
3. Oriented per-cell projected width → directional FAD (breaks symmetry).
4. Baseline-vs-scenario FAD ratio per direction (comparative framing).
5. Inverse-FAD cost surface + scipy Dijkstra corridor map.
6. Podium/ground-gap handling in the massing model so the lever is visible.
7. Greening roughness penalty on corridor cost.

### Key findings

- The current FAD screen is *exactly* symmetric about N–S and E–W, so NE ≡ SW; the published prevailing wind is NE (40.8% NE/ENE/E), so the screen is blind on the axis that matters ([AVRG73](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf)).
- TC 1/06 provides **no absolute benchmark** and explicitly enables comparison of design options — which legitimises a comparative screen and forbids a pass/fail verdict ([TC 1/06](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf)).
- TC 1/06 states CFD may be used "with caution" and that there is "no internationally recognized guideline or standard for using CFD in outdoor urban scale studies" — so a geometry screen is not a lesser AVA, it is a different, permitted tier.
- FAD can be broken out of axis-alignment cheaply by computing true per-cell projected width from real footprint orientation — pure numpy, milliseconds.
- Ventilation and heat are one geometry: wind is the advective heat sink and shares every design lever with shade (Ng & Cheng, 2009).

### Data summary

| Indicator | Value | Source |
|---|---|---|
| Prevailing wind, HSK Area 13 | NE/ENE/E = 40.8% of frequency | [AVRG73](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf) |
| Published velocity ratio, site (SVR) | baseline 0.28 → proposed 0.31 | [AVRG73](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf) |
| Published velocity ratio, local (LVR) | baseline 0.29 → proposed 0.32 | [AVRG73](https://www.pland.gov.hk/file/resources/ava_register/government/pdf/AVRG73_AVA_FinalReport.pdf) |
| VR definition | VR = Vp / V∞; V∞ at ~400–600 m | [TC 1/06 §4](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf) |
| Directions required | 16 (all required for Detailed Study) | [TC 1/06 §19, §21](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf) |
| Reference wind data | HKO Waglan Island | [TC 1/06 §11, §14](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf) |
| Number of massing envelopes screened | 2,639 | `citymodel_hsk.json` (repo) |

### Summary

Today's wind screen is an honest but direction-blind FAD: correct as a shape indicator, useless for telling NE from SW. Rasterising the footprints and computing true oriented projected widths breaks the symmetry at negligible cost; least-cost-path corridors on the resulting cost surface turn the number into a map a planner can act on. Both stay inside numpy-in-seconds, and both remain — explicitly — a comparative screening, never a velocity ratio.
