# CCR-TechDivePlanner

A PyQt6-based CCR dive planning application with Bühlmann ZHL-16C decompression modelling.

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
