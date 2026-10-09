# Design choices → heat, wind and drainage

**Started 9 Oct 2026.** Progress log at the bottom; findings first.

## The goal

Help a user understand what a design choice *does* — to heat, to ventilation, to
drainage. Not "here is a number" but "this choice, in this place, moves these
three things".

## The central finding: this is one feature, evaluated three times

The research produced the finding that reorganises the work. Heat, ventilation and
drainage look like three features. They are not. Per
*Policies and technical guidelines for urban planning of high-density cities —
air ventilation assessment (AVA) of Hong Kong* (Ng & Cheng, **Building and
Environment 44 (2009) 1478–1488**, the document behind Hong Kong's AVA policy):

> "For designers, it is possible to design our outdoor environment to **maximize
> wind speed and minimize solar radiation** to achieve comfort in the hot tropical
> summer months of Hong Kong." (§7 Wind for comfort)

Those are the only two variables a designer controls. And both are functions of
**the same geometry** — height, spacing, orientation, podium coverage and
ground-level permeability:

| Design lever | Affects solar/shade | Affects wind | Affects runoff |
|---|---|---|---|
| Building height | shade reach | frontal area, downwash | — (see caveat) |
| Building spacing / site coverage | sky view, shading | frontal area, blockage | impervious fraction |
| Orientation / alignment | sun on facades | directional frontal area | — |
| Podium | shading at ground | **blocks airflow (published)** | impervious |
| Ground-level permeability | — | "far more important than at high levels" | infiltration |
| Green / water cover | evapotranspiration | — | retention |

So the architecture is **one scenario model, three evaluators**. Building three
separate models would mean encoding geometry three times and letting them
disagree. This is also why the "wind channel" and "heatmap" are not two features:
they are two readings of one model.

## Findings — wind / AVA

All quotes below are from the AVA policy document above unless stated.

**The metric.** `VRw = Vp / VN`, where Vp is wind at pedestrian level (2 m) and
VN is wind at the top of the boundary layer (~500 m). *"The higher the value of
VRw, the less the impact [of the proposal]."*

**Reference bands** (empirical expectations, not thresholds):

> "urban wind velocity ratio … of **0.3 near the waterfront and in open spaces,
> and 0.05–0.1 in streets and congested urban spaces** can be expected."

**The criterion is relative, and that is authoritative.** This is the single most
important sentence for how this feature is built:

> "It is recommended that AVA be carried out for different design options to
> identify better design scenarios and potential problem areas, based on velocity
> ratio as an indicator. **A design having a higher velocity ratio would be
> considered as a better design than one having a lower velocity ratio.** At this
> stage, only the better design option is known, but whether the better design
> option meets a standard cannot be known."

There is no pass/fail band, by design. So the feature must **compare options to
each other**, and must never print a verdict that a scheme "passes". An earlier
version of our own screening said "no threshold is asserted" as a defensive
note; it is now a citation.

**The method matches what we already built.** *"For air ventilation assessment,
sixteen wind directions are considered. The VR of each direction (VRi) will then
be factored with the probability of wind coming from that direction."* Our
screening computes 16 directions and weights the prevailing ones. The official
method requires a full wind rose; we have one published frequency (NE/ENE/E =
40.8% from the AVRG73 assessment) and leave the other directions unweighted rather
than inventing them.

**The comfort conversion — this is what links wind to human experience.** Two
published numbers in the same document compose into a threshold we can compute:

| Published quantity | Value | Source |
|---|---|---|
| Mean roof-top (~100 m) HK urban canopy wind | ~2.5 m/s | §7 |
| Boundary layer (500 m) wind, extrapolated | 6–8 m/s | §7 |
| Comfortable pedestrian breeze, under shade, at 28 °C | **1.0–1.5 m/s** | §7 |

Comfortable VR ≈ 1.25 ÷ 7 ≈ **0.18**. So a street at VR 0.05–0.1 sits below the
breeze a shaded pedestrian needs, and ~0.18 is roughly where shade plus airflow
together achieve comfort. This is a *derived screening band from two cited
figures*, and the arithmetic lives in the code so it can be argued with.

## Findings — design levers, ranked by published impact

This is where the research overturned an assumption.

> "**Site coverage has a greater impact than that of building height** on [air
> quality]." (§6.2 Podium/site coverage)

Our most-used AI operation is `scale_all` — height. The document's own guidance
is that site coverage and podium matter *more* than height. That tension belongs
in the product, not in a footnote.

Other published rules, all scoreable from our geometry:

- **Varying heights beat uniform heights.** "it is better to have varying heights
  rather than similar/uniform height"; "a probable strategy for improving air
  ventilation is by varying building heights for diverting winds to the lower
  levels". (§6.4) Directly computable as a dispersion statistic.
- **Ground-level permeability dominates.** "The provision of permeability/gaps
  nearer to the pedestrian level is **far more important than that at high
  levels**. Create permeability in the housing blocks. Try to create voids at
  ground level." (§6.5)
- **Podium coverage blocks airflow.** "The podia with large site coverage not
  only block most of the [airflow]…" (§6.2)
- **Humidity is not worth modelling.** Givoni's studies in Japan and Israel, like
  Hong Kong, found *"relative humidity has been shown to have a statistically
  insignificant effect on comfort perception"*. A defensible screening can ignore
  it and say why.

## Findings — heat

There is **no equivalent of the VR bands for heat** in this document, and I am not
going to invent one. What the sources do give:

1. **Comfort is two-dimensional**: outdoor air temperature × solar radiation, with
   the neutral comfort region marked (Givoni chart, built on HK meteorological
   data, §7 Fig. 4). Wind is the moderator that lets a person tolerate more sun.
2. **The controllable levers are shade (minimise solar) and wind (maximise
   airflow)** — §7, quoted above.
3. **Our current heat index is hand-authored** (`HEAT_INDEX` in
   `_overlay_common.py`: paved 1.00, bare 0.85, green 0.40, wetland 0.20, water
   0.05). It is a land-cover lookup with **no published provenance**, and it is
   the weakest number in the project.

**So the honest heat model to build is a solar-access model, not another land-cover
lookup**: shade at pedestrian level as a function of height, spacing and
orientation, with sky view factor as the standard metric — then let the wind
number say whether a breeze is available to compensate.

## What is deliberately not claimed

- **Not CFD.** No flow solve, no turbulence closure, no mesh. Our FAD screening is
  geometry and is labelled as such.
- **Not a comfort prediction.** No UTCI, no PET, no SET. Those need air
  temperature and humidity time series we do not have, and a Givoni chart applied
  to a static wind rose would be a costume, not a comfort model.
- **No pass/fail verdict on any scheme.** The AVA document is explicit that this
  cannot be known yet; printing "PASS" would be inventing a standard.
- **Heat is not yet sourced.** The land-cover index stays labelled as
  hand-authored until a real land-surface-temperature or sky-view basis replaces it.

## Development plan

| Stage | What | State |
|---|---|---|
| 0 | Research, cited and recorded here | **done** |
| 1 | `design_metrics.py` — geometry-derived, each metric tied to a published rule: height dispersion, ground-level permeability, site coverage, swept-shadow fraction | **done** |
| 2 | One `evaluate(scenario)` returning heat + wind + drainage together, and no total | **done** |
| 3 | Scenario comparison — the relative judgement the AVA document asks for | planned |
| 4 | Replace the hand-authored heat index with a solar-access basis | needs a source |
| 5 | Expose to the LLM as an objective, not a number | planned |

Stage 2 is the one that matters most and the one easiest to get wrong: it invites
a single "score", and a single score across heat, wind and drainage is exactly
the kind of number nobody can defend. The three axes must stay separate, and any
ordering must state its weights.

## Progress log

- **9 Oct 2026, research** — Found the AVA policy document via a Building and
  Construction Authority paper that cites it, then extracted it with `pdftotext`
  after `web_fetch` returned raw PDF bytes. It supplies the VR definition, the
  empirical bands (0.3 open / 0.05–0.1 congested), the relative criterion, the
  16-direction method, the comfort conversion, and seven scoreable design rules.
  Established that heat, wind and drainage are one geometry read three ways.
  Progress file created.
- **9 Oct 2026, correction to an earlier claim** — I previously recorded the AVA
  criteria as "not extracted, no threshold asserted". The threshold was never in
  the Technical Circular to extract: the policy document says outright that
  whether a scheme meets a standard **cannot be known**. So the absence is the
  finding, not a gap in my work, and it is now cited rather than defended.
- **9 Oct 2026, Stage 1 built** — `app/services/design_metrics.py` and
  `GET /api/scenes/{sceneId}/design-metrics`. 37 tests.

  The shade figures first published with Stage 1 were **wrong by 2.3x** (46% and
  51% where the truth is 16% and 22%) and are corrected in Stage 2 below. The
  other Stage 1 figures stand: mean height 35.3 m, height variation CV 0.76, site
  coverage 30.2%, ground-level void fraction 94.8%, FAD mean 0.446 over sixteen
  directions.

- **9 Oct 2026, Stage 2 built** — `app/services/scenario.py` and
  `POST /api/scenes/{sceneId}/scenario/evaluate`. All three axes at once, and **no
  combined score**.

  Corrected geometry figures for the district: shade 16.2% at 09:00 and 22.1% at
  14:00 in December, 11.5% mean at the June solstice. A low-rise district at 22N
  genuinely has little pedestrian shade at midday — the high sun clears the
  street — and morning shade exceeds noon in December because the sun is still
  low in the east.

  The no-score decision has three independent reasons, and the third is the one
  that matters: the axes genuinely conflict. "Make the western blocks 40% taller"
  gives **more shade (+3.0 pp), more wind blockage (FAD +0.084), and no change at
  all to drainage**. A single score would settle that trade-off in whichever
  direction the weights leaned, without saying so. So the response returns three
  axes with their own units plus a `tradeoffs` list in plain language.

  Three more findings from building it:

  - **A direction label contradicted its own number.** A *rise* in frontal area
    density was reported as "less blockage". The cause was a hand-written
    if/elif chain, so the direction of each axis is now a single table,
    `AXIS_UP_MEANS`, stated once. The same drift that produced two copies of
    `projected_width_m` produced this.
  - **Rainfall is a forcing, not a scheme.** It shifts the baseline and the
    candidate equally, so it never appears in an axis delta — which made
    "what if it rains 100 mm more" return all-zero and look broken. It is now
    reported separately: at 550 mm the deficit rises by **780,289 m³**, which is
    the number the question was actually asking for.
  - **Height does not move drainage, and that is arithmetic.** A footprint is
    already impervious whatever its height, so "30% taller" returns a drainage
    delta of exactly zero. The response says so in the axis limitation rather
    than letting it look like a bug. Interventions are what move drainage.

  Three things worth recording, because all three were wrong first and the first
  two looked entirely plausible while they were wrong:

  - **Shade was first reported as 3.0%** on a winter afternoon, because the
    shadow walk marked one cell per step instead of sweeping each building's
    footprint.
  - **Sweeping the footprint then over-reported by 2.3x, at 51.2%.** The sweep ran
    every building out to the *tallest* building's shadow length, so a 5 m shed had
    its 30 m footprint painted right across the district. The shaded set
    saturated. This was found in Stage 2, not Stage 1, and the tell was a
    physically impossible result: raising the western 1,232 envelopes gave
    byte-identical shade to raising all 2,639. **Every ordering check passed
    throughout** — summer below winter, morning below noon — because a saturated
    sweep is still monotone in the right directions. Ordering was not enough;
    magnitude was the signal, and only a comparison between two different schemes
    exposed it.
  - **The sweep took 22 seconds** because it issued one NumPy assignment per
    building per step. Grouping by footprint span made it 0.01 s.

  The report prints `verdict: null` and says why: the AVA policy states that
  whether a scheme meets a standard cannot be known, so there is nothing to pass.

## What Stage 2 settled, and what is still open

Stage 2 produced the finding the plan predicted: the axes conflict, and the
conflict is the content. Raising heights shades more ground and blocks more wind;
green space lowers the deficit and does nothing to shade or ventilation; a forcing
change moves neither scheme axis. There is no ordering of these that is true
without saying what you weight.

**Still open, in the order I would do it:**

- **Stage 3, comparison.** `compare()` must rank schemes with *stated* weights,
  and must refuse to rank when the weights are not supplied. A default weight set
  would quietly become the project's opinion about which axis matters, which is
  the thing the AVA guidance says cannot be settled.
- **Stage 4, the heat index.** `HEAT_INDEX` is still hand-authored with no
  published provenance. It is now the weakest number in the project and it sits in
  the drainage/retention path, so it should be sourced or replaced.
- **Stage 5, the LLM as policy.** With three real axes, the planner can state an
  objective — "cut the deficit here without losing shade" — and the evaluator can
  report what that costs on each axis. That is a division of labour where neither
  half can silently corrupt the other.
- **`place` and `reset` cannot yet be scored.** Both need the city-model
  generator, and reimplementing either in the evaluator would drift from it. The
  evaluator says `unsupported` rather than approximating, which is the right
  answer today and a real gap by the demo.
