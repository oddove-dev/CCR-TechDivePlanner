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
- Bailout OC gas planning
- Tissue saturation charts and heatmaps
- Gas consumption calculations
- Gas calculation tab *(work in progress — not yet functional)*
- Dive profile visualisation (matplotlib)
- Save/load multiple dive profiles

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
