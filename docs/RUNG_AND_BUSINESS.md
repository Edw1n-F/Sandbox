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
| **C** ★ | The interactive sandbox, real terrain and land cover, the storage ledger and wet grid, the heat index, grounded AI interventions, and **AI-driven 3D placement and alteration scored against the ledger**. Sea-level scenarios. Validation by monotonicity and unit tests. **This is us.** |
| **D** | A static map with two toggles and a number. The minimum that still demonstrates the idea. |

### What rung C deliberately leaves out, and why

Each omission is a decision, not an overrun. Saying so is the point.

| Not built | Why |
|---|---|
| **Typhoon surge + jacking effect** (task 12) | Needs an Hato tide series and an outfall network we do not have. This is the honest reason the brief itself gives for the whole project being at risk: the two things that "can still break it" are LiDAR access and 2017 validation data |
| **Perception encoder** (task 16) | Requires a trained segmentation model. We use **ESA WorldCover 10 m v200**, an existing pretrained global product, and say so rather than implying we trained anything. This is the rung B/C boundary |
| **Generative emulator** (task 17) | A surrogate needs training pairs from a slow simulator. Our ledger runs in **0.9 s**, so there is nothing to make faster — the premise of an emulator does not apply at this scale |
| **Kappa vs recorded flood zones** (task 15) | No 2017 validation dataset is available. The brief accepts this: *"Validate with the ladder and report the disagreement."* We report the ladder |
| **InVEST** (task 8) | We implemented SCS-CN directly from USDA TR-55 Table 2-2 rather than installing InVEST. Deviation noted below |
| **CEDD LiDAR bare-earth DTM** (task 13) | Access is request-gated. We use Open-Meteo's SRTM-derived elevation, and the panel labels it |

### What rung C has that rung B does not

The AI 3D work. Placing massing on real land cover, altering it by scope, and
scoring every candidate change against the storage ledger — with the language
model stating the objective and the code doing the arithmetic. That is the part
that is ours rather than assembled, and it is why this is C and not B.

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
   Deep Bay, where the brief's own claim is that it has never been run.
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
- **Footprints are procedural, not surveyed.** OSM is unreachable from this host,
  so massing is derived from ESA WorldCover and flagged provisional throughout.
- **Single-building changes are ~0.006% of the district total.** The tool is for
  choosing what to do to one building; district claims need aggregation across
  many, which the scenario runner does.
- **Heat is an unverified hand-authored index** — not degrees, not an
  air-temperature field.
- **The DEM is ~90 m cells.** Ground elevation is a neighbourhood value, not a
  spot height.

---

## Part 3 — Documented deviations

Three places where we did something other than what the brief specified. None is
hidden; all three are choices.

| Brief says | We did | Why |
|---|---|---|
| "InVEST / UFRM ... **We use it rather than writing our own**" | Implemented SCS-CN from TR-55 Table 2-2 directly | The brief's own reasoning is reproducibility, and a cited table read by our own code is *more* auditable than an opaque library call. It also removes a heavy dependency from a demo that must run offline. The citation for every class is in `scripts/_overlay_common.py` |
| "Access checks: CEDD LiDAR — instant or request-gated?" | Did not wait; used Open-Meteo SRTM-derived elevation | Request-gated data would have blocked the demo. The panel labels the terrain source at all times. This is a rung C substitution and is declared as one |
| "Training an AI to trace ponds / gei wai / mangroves" | Used ESA WorldCover v200 | We did not train a perception encoder. WorldCover is a pretrained global product and is credited as such. Claiming otherwise would be the single easiest thing for a judge to catch |
