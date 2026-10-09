# 4. Design-Choice Sensitivity: One Intervention Model Driving Coupled Heat, Wind and Drainage Deltas for the HSK/HT Sandbox

## The organising finding: one geometry, read three ways

Heat, ventilation and drainage are usually built as three features backed by three models. In the HSK/HT sandbox they are **one geometry read three times**. The controlling source is the study behind Hong Kong's Air Ventilation Assessment framework ([Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/)). Its section 7 states the design problem in a single sentence: "For designers, it is possible to design our outdoor environment to **maximize wind speed and minimize solar radiation** to achieve comfort in the hot tropical summer months of Hong Kong." A designer therefore controls exactly two comfort variables, and both are functions of the same massing — height, spacing, orientation, podium coverage and ground-level permeability. Because those two comfort variables and the runoff-generation term all read the *same* footprint-and-height raster, one scenario model can drive all three axes. Building three models would encode that geometry three times and let the three answers disagree — the failure this chapter exists to prevent.

## The lever → axis matrix

Every cell below cites a published rule, which is what makes the integration defensible rather than a preference.

| Design lever | Heat (solar / shade) | Wind (frontal area / flow) | Drainage (runoff) | Published basis |
|---|---|---|---|---|
| **Building height** | Shade reach grows with height | Frontal area and downwash — but height alone is the *weaker* lever | No direct land-cover effect | [Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/) §6.2, §6.4 |
| **Spacing / site coverage** | Sky view up, shading down as coverage rises | **Greater impact than building height**; blockage | Impervious fraction up → CN up | [Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/) §6.2 |
| **Orientation / alignment** | Sun on facades varies with alignment | Directional frontal area | — | [FAI review, 2022](https://www.sciencedirect.com/science/article/pii/S0360132322008186) |
| **Podium** | Ground-level shading | **Blocks most airflow at large coverage** | Impervious | [Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/) §6.2 |
| **Ground-level permeability** | — | **"Far more important than at high levels"** | Infiltration | [Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/) §6.5 |
| **Green / water cover** | Evapotranspiration + albedo | Surface roughness | Retention + lower CN | [Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/) §7; [USDA NRCS TR-55](https://www.hydrocad.net/pdf/TR-55%20Manual.pdf) |

## The published rankings that overturn assumptions

The literature does not weight the levers equally, and several rankings contradict intuition. **Site coverage has a greater impact than building height** on airflow ([Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/), §6.2); **varying heights beat uniform heights** (§6.4); **ground-level permeability dominates** (§6.5); and **podia with large site coverage block most of the airflow** (§6.2). Humidity, meanwhile, can be defensibly dropped: Givoni's Japanese and Israeli studies found relative humidity has a "statistically insignificant effect on comfort perception," a result later confirmed specifically for Hong Kong subjects ([Chow et al., 2010](https://www.sciencedirect.com/science/article/pii/S0360132310001058)).

This creates a real tension worth surfacing rather than burying. The prototype's most-used AI massing operation is a uniform height scale, yet height is the *weaker* lever and uniformity is the *worse* case the guidance names ([Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/), §6.4). The product should tell the user this: raising every block by the same amount is the least effective thing they can do.

## The delta contract: three deltas, never one score

Each design edit produces **three separate deltas against the same baseline**, never a blended number:

1. a **heat delta** — change in the relative index and the SVF/shade term;
2. a **wind delta** — change in the directional frontal-area-density ratio plus the corridor change;
3. a **drainage delta** — change in unstored volume (the buffer deficit), exactly as `ledger_for_scenario` reports it.

If a single composite is ever shown, its **weights must be stated** and the three axes must remain separately visible. The reason is not fastidiousness: the three axes are *incommensurable* — a dimensionless 0–1 index, a dimensionless area ratio, and a volume in cubic metres. Combining them requires normalisation and weighting choices that no published standard supplies, and the OECD/JRC guidance is explicit that composite indicators are sensitive to those choices and can mislead when components are not substitutable ([Nardo et al., 2008](https://doi.org/10.1787/533411815016)). A drainage loss cannot "compensate" for a ventilation gain. The AVA framework makes the same point from the other side: it offers only a *comparative* judgement and states that whether a scheme meets a standard "cannot be known" ([Technical Circular 1/06](https://www.devb.gov.hk/filemanager/en/content_679/hplb-etwb-tc-01-06.pdf)).

## Mapping the existing intervention vocabulary onto the three axes

The prototype's `INTERVENTION_EFFECTS` table already fixes what each lever does, and it maps cleanly:

- **`green_space` / `urban_green`** → heat (evapotranspiration, albedo) **and** drainage (lower CN), plus a slight wind roughness term.
- **`water_reservoir` / `flood_storage`** → heat (coolest surface) **and** drainage (retention).
- **`permeable_paving`** → drainage via the CN-72 override; the heat benefit is via **albedo and surface temperature**, and this is stated as an *assumption* consistent with the permeable-pavement thermal literature ([Building and Environment, 2026](https://www.sciencedirect.com/science/article/pii/S0360132325014180)), not a measured local effect.
- **`massing_height`** → heat **and** wind only, and only *once* the SVF/shade and oriented-FAD upgrades exist. Today it has **no grid effect at all**, by design: a height change moves shade and exposure, never a land-cover cell, so a ledger claiming a flood benefit from raising a building would be lying.
- **`drainage_alignment`** → reports **zero**, and that is the honest answer: whether a network helps depends on pipe and outfall capacity this screening model does not have.

The practical consequence is that **the heat and wind upgrades are what make `massing_height` visible**. Until they land, the most-used lever moves nothing.

## How to present this to a user

Present a **per-lever delta readout** — three columns, one per axis — beside a **baseline-vs-scenario comparison**, and state plainly **what changed and what did not**. The prototype's existing honesty pattern is the model: every response already carries its own caveats, and the drainage ledger already emits `interventionsSkipped` with a reason for each lever it declined to score. Reuse that shape for all three axes so an absence is never read as an oversight.

## Validation

Two tests make this trustworthy. **Monotonicity per axis**: paving more must worsen drainage; adding obstruction in the prevailing direction must not improve ventilation; more shade or green must lower the heat index. **A frozen golden scenario** — one stored intervention set with pinned heat, wind and drainage deltas — so any regression surfaces as a failing test rather than a quietly different number.

## What to build next (dependency order)

1. One `evaluate(scenario)` returning heat + wind + drainage together, sharing one geometry pass.
2. The heat upgrade (SVF + solar shadow) so `massing_height` becomes visible.
3. The wind upgrade (oriented per-cell projected width + corridor FAD ratio) to break the NE≡SW symmetry.
4. The three-column delta readout with baseline-vs-scenario comparison.
5. Monotonicity tests and the frozen golden scenario.
6. A composite score *only if* weights are exposed and the three axes stay visible.

### Summary

Heat, wind and drainage in the HSK/HT sandbox are one geometry evaluated three times, and the AVA policy paper says why: the designer controls only two variables — minimise solar radiation, maximise wind speed — both functions of the same massing ([Ng & Cheng, 2009](https://pmc.ncbi.nlm.nih.gov/articles/PMC7117041/)). The integration layer is therefore one scenario model driving three separate deltas, with site coverage, height variation and ground-level permeability ranked above height, and with no single blended score unless its weights are stated. That is what lets one design edit — a raised block, a podium, trees, a basin, permeable paving — be judged three times by the same geometry, honestly and separably.
