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
- Tissue saturation charts and heatmaps
- Gas consumption calculations for both CCR and bailout plans
- Dive profile visualisation with overlaid CCR and bailout profiles (matplotlib)
- Background simulation — UI stays responsive during profile switches
- Save/load multiple dive profiles
- Gas calculation tab *(work in progress — not yet functional)*

## Bailout Modes

The bailout planner models a parallel worst-case OC scenario alongside the CCR plan. Two modes are available, controlled by the **"Bailout time before CCR ascent"** setting and the **"Ascend immediately"** checkbox:

### Mode 1 — Bail out, wait, then ascend (default)

The diver bails out to OC *X* minutes before the planned CCR ascent, spends those *X* minutes at depth on OC gas, then begins the OC ascent at the same time the CCR plan ascends. Total bottom time is unchanged. This models a realistic emergency where the diver needs time to assess the situation and prepare before ascending.

- Chart: both profiles flat at bottom depth until CCR ascent time, then diverge
- Buoyancy snapshot shown at the bailout switch point (before any OC gas consumed)

### Mode 2 — Bail out and ascend immediately

The diver bails out to OC *X* minutes before the planned CCR ascent and immediately begins ascending — no time spent at depth on OC. The OC ascent starts *X* minutes earlier than the CCR ascent.

- Chart: CCR profile stays flat until its ascent time; bailout profile diverges upward *X* minutes earlier
- Buoyancy snapshot shown at the bailout switch point (same logic as Mode 1)
- Enable with the **Ascend immediately** checkbox in Settings → Bailout

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
