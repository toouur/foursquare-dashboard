# BUG-008 — Naive-Bayes layer reclassifies every bicycle segment as Train

| | |
|---|---|
| **Severity** | Major (systematic misclassification: 194/194 bike segments wrong on the trips map) |
| **Priority** | Medium |
| **Status** | Fixed + regression-tested |
| **Component** | `scripts/transport_mode.py` (self-training NB adjudication layer) |
| **Environment** | Static build, transport-mode inference over ~48.9 k trip check-ins |
| **Found via** | Distribution sanity check after adding the NB layer — the B (bike) class count dropped from 194 to 0 |

## Description

Transport-mode inference is a rule cascade (FR24 flight windows → category anchors →
speed/distance bands) with a self-training Gaussian Naive-Bayes layer on top: each build
it harvests weak labels from the rule anchors and re-adjudicates ambiguous segments using
`log10(speed)` and `log10(distance)` features.

In its first version the NB layer was allowed to adjudicate **any** band-classified
segment. The weak-label training set is wildly imbalanced: ~200 bike (B) samples vs
~4,800 train (T) samples, and the two classes overlap heavily in the speed/distance
feature space (a cyclist and a stopping metro both average 10–25 km/h over a few km).
With class priors ~24:1, NB rationally assigned essentially every bike-band segment to
T — **all 194 🚴 segments in history became 🚇 in one build**.

## Steps to Reproduce (pre-fix)

1. Build with the NB layer allowed to adjudicate band-B segments.
2. Compare the mode distribution before/after: `B: 194 → 0`, `T` grows by the same amount.
3. Open any known cycling trip on the trips map — every segment renders with the train style.

## Expected

The learning layer refines only genuinely ambiguous segments; a thin, distinctive class
(bike trips are known context, not a statistical guess) is never wholesale absorbed by a
fat neighboring class.

## Actual

Every bike segment reassigned to train. Not a subtle drift — total class annihilation,
which is exactly what class-prior arithmetic predicts for a 1:24 imbalance with
overlapping features.

## Root cause

An imbalanced self-trained classifier was given jurisdiction over classes it cannot
discriminate. NB with class priors will always sacrifice a thin class inside a fat
class's feature region — the math worked exactly as designed; the *scope* of what it was
allowed to overrule was wrong.

## Fix

NB jurisdiction was narrowed to the one genuinely ambiguous slice: it adjudicates **only**
segments the band cascade labeled `C` (car) with 7 ≤ v < 150 km/h — never rule anchors,
never the walk band, and **never bike-trip band-B segments**, which are protected
outright. Everything else keeps its cascade result. The layer also falls back to plain
bands unless it has ≥ 2 classes × ≥ 8 samples.

## Regression coverage

`tests/test_transport_mode.py` (45 tests) pins the cascade order and the NB gate: anchor
results are never overridden, band-B survives adjudication, and the fallback triggers on
thin training data.

## Lessons

For ML layered over rules, the critical design decision is *jurisdiction*, not accuracy:
define exactly which decisions the model may overrule, and protect thin classes from fat
ones explicitly. And always diff the class distribution before/after enabling a
classifier — a healthy-looking build with one class silently at zero is the failure mode
to expect, not an edge case.
