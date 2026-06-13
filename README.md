# CCR-TechDivePlanner

A PyQt6-based CCR dive planning application with Bühlmann ZHL-16C decompression modelling.

## Overview

This application serves two interconnected purposes for CCR technical divers: buoyancy planning and decompression planning.

### Buoyancy Planner

The buoyancy planner gives the diver a complete picture of their overall buoyancy — accounting for the diver, exposure suit, CCR unit, and all stage/bailout cylinders. The diver can model how cylinder choice and gas consumption affect buoyancy throughout a dive, allowing them to configure a rig that is appropriately negative at the start without being unnecessarily heavy for typical dives.

### Decompression & Bailout Integration

Once a buoyancy configuration is set, the diver can build a dive profile and simulate a bailout scenario. The decompression planner calculates required stops and gas usage, while the buoyancy planner tracks whether the diver maintains sufficient negative buoyancy as cylinders are consumed during ascent.

For unusually long bailouts, the application supports modelling cylinder drops — where empty bailout bottles are sent to the surface on an SMB or handed to a buddy. This allows the diver to verify that buoyancy remains manageable even in rare, worst-case scenarios, without having to dive excessively heavy on every normal dive.

## Features

- Buoyancy planning with cylinder and equipment database
- CCR dive planning with configurable setpoints (descent, bottom, deco)
- Bühlmann ZHL-16C decompression with gradient factors (GF Low/High)
- Bailout OC gas planning with two bailout modes (see below)
- **Optimal Bailout optimizer** — searches OC gas combinations for the best bailout/deco mixes against your dive, with a Pareto front and multi-core simulation (see below)
- Tissue saturation charts and heatmaps
- Gas consumption calculations for both CCR and bailout plans
- Dive profile visualisation with overlaid CCR and bailout profiles (matplotlib)
- Background simulation — UI stays responsive during profile switches
- Save/load multiple dive profiles
- Gas calculation tab *(work in progress — not yet functional)*

## Bailout Modes

The bailout planner models a parallel worst-case OC scenario alongside the CCR plan. Two modes are available, controlled by the **"Bailout time before CCR ascent"** setting and the **"Ascend immediately"** checkbox:

### Mode 1 — Bail out, wait, then ascend (default)

The diver bails out to OC *X* minutes before the planned CCR ascent, spends those *X* minutes at depth on OC gas, then begins the OC ascent at the same time the CCR plan ascends. Total bottom time is unchanged. Use this mode when you want to start the ascent from the **same point and the same time at depth** as the planned CCR dive — for example to return to a fixed ascent location such as an upline or shotline. Because the diver breathes open circuit at depth for those extra *X* minutes, this mode consumes **more bailout gas** than Mode 2.

- Chart: both profiles flat at bottom depth until CCR ascent time, then diverge
- Buoyancy snapshot shown at the bailout switch point (before any OC gas consumed)

### Mode 2 — Bail out and ascend immediately

The diver bails out to OC *X* minutes before the planned CCR ascent and immediately begins ascending — no time spent at depth on OC. The OC ascent starts *X* minutes earlier than the CCR ascent. By minimising time at depth on open circuit, this mode consumes **less bailout gas** than Mode 1. It can model a direct **open-water ascent** rather than returning to a fixed upline/shotline, saving further bailout gas.

- Chart: CCR profile stays flat until its ascent time; bailout profile diverges upward *X* minutes earlier
- Buoyancy snapshot shown at the bailout switch point (same logic as Mode 1)
- Enable with the **Ascend immediately** checkbox in Settings → Bailout

## Optimal Bailout Optimizer

The **Optimal bailout** tab searches across open-circuit gas combinations to find efficient bailout/deco gas sets for the current dive. Each candidate is run through the same full Bühlmann bailout simulation as the planner, then ranked.

### How it works

- **Per-cylinder modes** — each bailout/deco cylinder can be set to:
  - **Optimize** — vary its O₂/He across the grid
  - **Use fixed** — pin it to a specific mix you type (e.g. a candidate you discovered, or a gas not in your planner). Type a mix into an empty slot to add an extra fixed gas.
  - **Remove** — leave it out of the dive
  - Defaults follow the dive plan: a cylinder that isn't part of the plan defaults to **Remove**.
- **Constraints** — configurable O₂/He grid step, Max PO₂ per cylinder, Max EAD (helium-only-when-needed narcosis rule), Max ΔPN₂ (isobaric counterdiffusion at gas switches), and a combination cap.
- **Auto-fit Min PO₂** — sets each active cylinder's Min PO₂ to the richest mix its grid can reach, so a run is never emptied by a too-high Min.
- **Objectives & Pareto front** — results are sorted by configurable primary/secondary objectives (surface time, deco time, bailout/total gas usage) with the Pareto-optimal front highlighted on a colour-coded plot (colour = bailout He%).
- **Multi-core** — large runs are parallelised across CPU cores (`Auto` = all but one); each combination is an independent full deco simulation.

### Comparing candidates

- **★ Rank 0** — your current planner bailout plan is shown as a reference row, recomputed against the current dive on every run (never stale).
- **📌 Keep** — pin any result row to keep it across runs for side-by-side comparison; kept rows are re-simulated against the current dive so they stay comparable.
- Click a **Rank** number to open the full bailout plan (stops, runtime, gas, PO₂/PN₂/ΔPN₂, EAD, buoyancy) for that candidate.

## Requirements

- Python 3.10+
- PyQt6
- matplotlib
- numpy

## Install

```bash
pip install PyQt6 matplotlib numpy
```

## Run

```bash
python main_qt.py
```
