# Which rung we are on, and how this pays for itself

Two documents the source brief asks for and the repository did not have: the
**rung declaration** (task 18, due 11 Oct) and a **business model** (worth 20% of
the judging rubric, and absent from every file we had).

Both are written here rather than in the README because they are answers to
judges, not build instructions.

---

## Part 1 — The rung declaration

The brief says: *"Declare the fallback rung (A / B / C / D). Say out loud which
version of the project we are actually delivering. Rung A is everything; rung D is
the minimum. Choosing a lower rung deliberately is a good outcome, not a failure
— the failure is not saying so until the deadline."*

It also says the deadline is 11 Oct. We are declaring on 9 Oct, and **we are on
rung C.**

### The rungs, defined

The brief never defines what each rung contains — it references them four times
and specifies none. So these are ours, written against the brief's own task list:

| Rung | Contains |
|---|---|
| **A** | Everything in the task list: baseline storage ledger, InVEST runoff, three real published plans, typhoon surge with jacking, HAND, a trained perception encoder, a generative emulator with a reported ensemble, and kappa validation against recorded flooding |
| **B** | A, but with **ready-made HAND tiles instead of a derived bare-earth DTM**, and a **documented disagreement** against whatever validation data exists |
| **C** ★ | The interactive sandbox, real terrain and land cover, the storage ledger and wet grid, the heat index, grounded AI interventions, the **buffer deficit as a served headline number**, **sea-level scenarios run against the official HKO projections**, and **three published CEDD programmes encoded as scenarios**. Validation by monotonicity and unit tests. **This is us.** — *Amended 14 Oct 2026: the first version of this row also claimed "AI-driven 3D placement and alteration scored against the ledger". That capability was **removed**, not delivered: the procedural massing generator was replaced by 396 measured Open3Dhk + Overture footprints, and `place` was taken off the natural-language front door. Massing ops are still scored, but nothing generates one from an instruction. See `STATUS.md`.* |
| **D** | A static map with two toggles and a number. The minimum that still demonstrates the idea. |

### What rung C deliberately leaves out, and why

Each omission is a decision, not an overrun. Saying so is the point.

| Not built | Why |
|---|---|
| **Typhoon surge + jacking effect** (task 12) | Needs an Hato tide series and an outfall network we do not have. This is the honest reason the brief itself gives for the whole project being at risk: the two things that "can still break it" are LiDAR access and 2017 validation data |
| **Perception encoder** (task 16) | Requires a trained segmentation model. We use **ESA WorldCover 10 m v200**, an existing pretrained global product, and say so rather than implying we trained anything. This is the rung B/C boundary |
| **Jacking effect, as a drainage model** (task 12) | The sea-level scenarios implement the *submergence* half of jacking, and report that it is inert at this site. The half that matters — a submerged outfall — needs pipe and outfall capacity we do not have. Claiming it would require inventing that network |
| **Generative emulator** (task 17) | A surrogate needs training pairs from a slow simulator. The ledger arithmetic runs in **0.9 s** (a full browser run is ~11 s end to end, most of it rendering), so there is nothing to make faster — the premise of an emulator does not apply at this scale |
| **Kappa vs recorded flood zones** (task 15) | No 2017 validation dataset is available. The brief accepts this: *"Validate with the ladder and report the disagreement."* We report the ladder |
| **InVEST** (task 8) | We implemented SCS-CN directly from USDA TR-55 Table 2-2 rather than installing InVEST. Deviation noted below |
| **CEDD LiDAR bare-earth DTM** (task 13) | Access is request-gated. We use the **5 m Open3Dhk/LandsD terrain tiles**, and the panel labels it. (Superseded in-place: this row previously said "Open-Meteo's SRTM-derived elevation" — that ~90 m global model is the *retired* source, replaced on 14 Oct 2026. See `DATA_SOURCES.md`.) |

### What rung C has that rung B does not

The AI 3D work. Placing massing on real land cover, altering it by scope, and
scoring every candidate change against the storage ledger — with the language
model stating the objective and the code doing the arithmetic.

And now the numbers that make the sandbox mean something rather than demo
something:

- **The buffer deficit, served.** 180,566.3 m³ — today's unstored water on the
  current **43.5 ha** window, pinned by test to what `make_wetgrid.py` already
  produced. `GET .../ledger` adds the delta a scenario makes to it.
  *(The retired 786 ha extent reported 3,170,180.3 m³ for the same metric; the
  research chapters still quote it. Different extents, not a correction.)*
- **Three published CEDD programmes**, from the official Development Blueprint:
  First Phase ~19.6 ha, Second Phase Stage 2 ~200 ha, and all six Second Phase
  contracts ~253.5 ha. Which produced the most useful finding we have: **the
  published Second Phase does not fit the land available.** Measured on the
  original ≈786 ha extent, it was larger than the non-paved land remaining, and
  bare ground ran out after 127 ha. That falls straight out of using CEDD's number
  instead of an invented one. On the current 43.5 ha window the programmes still
  do not fit the available land (measured shortfalls 15.4, 193.7 and 247.2 ha),
  but the 127 ha bare-ground figure above belongs to the old extent.
- **Sea-level scenarios against the official projections** — HKO's SSP2-4.5
  37–82 cm and SSP5-8.5 57–108 cm ranges, cited and retrieved, with the honest
  result that none of them changes the deficit at this site and the reason why.
- **A chat that explains a run and shows its work.** The answer is a card, not a
  paragraph, and every number in it is traced back to the run by code — a card
  that cannot be grounded is sent back once and then shown *with* the warning.
  This is the "can GenAI help planners explore responsibly" question the brief
  asks, answered in the only way that survives contact with a real planner: the
  model writes the prose, the code checks the arithmetic, and neither is allowed
  to do the other's job. With no model configured the same card is assembled by
  code and **labelled** as such, so the demo cannot pass a printoff as an
  interpretation.

---

## Part 2 — Business model

*Feasibility and impact is 20% of the rubric. It asks twice: "What is the
business model or plan?" and "Is the solution practically implementable given
real-world constraints?"* Nothing we had answered either.

### Who pays, and for what

**The paying customer is a planning consultant preparing a land-use and
environmental impact assessment for a Northern Metropolis development, and the
person who signs it off is the assessor's EPD case officer.**

That is a real constraint, not a guess: the brief's own environmental section
complains that *"a lot of the problem evaluation is only conducted on a
project-by-project basis... the projects aren't evaluated together."* A
cumulative tool has no natural buyer until someone must produce a cumulative
assessment. So the product is not "flood prediction" — that market already has
HEC-RAS, LISFLOOD-FP and DELFT-FEWS, and we would lose to them.

The product is **the cumulative comparison the current process cannot produce**,
as a screening step in front of the expensive assessment.

### What we charge for, and what we do not

| Tier | What it is | Price basis |
|---|---|---|
| **Free — public sandbox** | The public model, the documented method, the assumption register, every number with its source | Free, permanently. The method is the argument for adoption |
| **Per-engagement** | A cumulative screening run for one development against one alternative layout: a delta table and a short memo | Consultant day-rate equivalent; two to three days of work |
| **Subscription — the real business** | The tool inside the consultancy's workflow, with their scenarios, their projects, their audit trail | Per-seat, per year |

The free tier is not generosity, it is the sales mechanism. A consultant who has
run the public model on their own site has already done the hardest part of
adopting it.

### Why this is defensible to a judge

1. **We are not inventing a metric.** Compensatory flood storage is already
   mandated in other jurisdictions. We are running a standard screening test on
   **Hung Shui Kiu / Ha Tsuen**, where the brief's own claim is that it has never
   been run. *(The site was switched from Deep Bay to HSK/HT on 6 Oct 2026 — the
   27-task plan points at Deep Bay, but HSK/HT has the stronger public evidence
   base and the Open3Dhk tile coverage. See `CLAUDE.md` and `DATA_SOURCES.md`.)*
2. **We do not displace the statutory process.** Everything we produce is
   labelled preliminary and screening-level, explicitly not engineering grade,
   and the API says so in every response. A tool claiming to replace EPD
   assessment would be both wrong and unsellable.
3. **The comparison is the product.** Single-project assessment already exists.
   Cumulative, pre-statutory, with uncertainty shown, is the gap our interview
   notes keep circling.

### Sustainability and ethics, in the same register

| Question | Our answer |
|---|---|
| Financial sustainability | Consulting revenue funds the free tier. There is no venture-scale claim and we do not make one |
| Scalability | Pure numpy on a file store, no GPU and no model server. It runs on a laptop, which is why a small planning team can afford it |
| Ethical concerns | **Bias in the curve-number table** is the big one, and we publish the ±20% sensitivity band rather than a single figure. **False precision** is the second, and every record carries its own limits. **Displacement** is the third: a planner who trusts a screening number as a statutory one causes harm, which is why the labelling is not optional |
| Community feedback | Not yet gathered. Stated as a gap rather than implied |
| Empowerment | The tool shows the trade-off; it does not pick. The recommendation is a ranked list with the arithmetic visible, not an answer |

### Honest weaknesses a judge will find

Better to name them than be found out on:

- **The Curve Number table is the largest single lever on every number.** We
  publish the sensitivity band; the number is still an assumption.
- **Footprints are measured geometry, not surveyed storeys.** The 396 footprints
  come from Open3Dhk tiles fused with Overture, and heights are the 90th
  percentile of tile surface minus tile terrain. Kept provisional and disclosed
  throughout: never described as surveyed storeys. *(The 2,639-footprint
  procedural massing model this row used to describe was deleted on 14 Oct 2026.)*
- **Single-building changes are ~0.006% of the district total.** The tool is for
  choosing what to do to one building; district claims need aggregation across
  many, which the scenario runner does.
- **Heat is a screening surface-temperature field, not a measurement.** It is
  anchored to an observed Landsat LST scene and a cited HKO air-temperature band,
  and the geometry terms move cells in °C away from the site mean. Air
  temperature is an *estimate* (surface − 12 °C), not a modelled field.
  *(This row previously read "an unverified hand-authored index — not degrees".
  That described the pre-LST relative 0–1 index, which the LST anchor replaced.
  See `DATA_SOURCES.md` §4 and the shipped sidecar's
  `SURFACE_TEMPERATURE_ANCHORED_TO_LST`.)*
- **The DTM is the 5 m Open3Dhk/LandsD terrain-tile mesh surface** — real 5 m
  posts, 117 × 148 over the window. It is a mesh surface, not a surveyed
  bare-earth release, and a run re-solves the water level on it without
  re-conditioning it. *(This row previously read "The DEM is ~90 m cells", which
  described the retired Open-Meteo SRTM source.)*

---

## Part 3 — Documented deviations

Three places where we did something other than what the brief specified. None is
hidden; all three are choices.

| Brief says | We did | Why |
|---|---|---|
| "InVEST / UFRM ... **We use it rather than writing our own**" | Implemented SCS-CN from TR-55 Table 2-2 directly | The brief's own reasoning is reproducibility, and a cited table read by our own code is *more* auditable than an opaque library call. It also removes a heavy dependency from a demo that must run offline. The citation for every class is in `scripts/_overlay_common.py` |
| "Access checks: CEDD LiDAR — instant or request-gated?" | Did not wait; used Open-Meteo SRTM-derived elevation | Request-gated data would have blocked the demo. The panel labels the terrain source at all times. This is a rung C substitution and is declared as one |
| "Training an AI to trace ponds / gei wai / mangroves" | Used ESA WorldCover v200 | We did not train a perception encoder. WorldCover is a pretrained global product and is credited as such. Claiming otherwise would be the single easiest thing for a judge to catch |
