# Vernadsky — three orbits (prototype)

Live preview: https://nikita-gorban.github.io/vernadsky-proto/ (lite version for phones: [`?src=frames_lite/`](https://nikita-gorban.github.io/vernadsky-proto/?src=frames_lite/))

A fly-around of the building at three heights: swipe left-right to rotate, up-down to switch orbit; the angle stays the same.

The repository holds two sets of frames:

- `frames/` — full quality, 1280 px, one file per frame (about 290 MB). Loaded by default.
- `frames_lite/` — lightweight, 800 px, packed into `pack_*.bin` (about 100 MB). For phones: `index.html?src=frames_lite/`.

Orbit alignment can be checked in `frames/calibration.jpg`.

Orbit order top to bottom (swipe up = higher): 3, 1, 2. Starts on orbit 1. Set by the order in `frames/orbits.json`.

Frame sources: 001 — `CC_Pano360_001_####.jpg`, 002 — `CC_Panorama360_002####.jpg`,
003 — `CC_Aerial_Selecter_panorama360_003_02/CC_Panorama360_003_####.jpg`. All three start at the same angle (offset 0).

## How it works

Camera position is a single value: the fraction of a revolution `turn` (0…1). It is shared by all three orbits.
The orbit frame for it: `k = offset + (reverse ? −1 : 1) · round(turn · count)` mod `count`.
Switching orbits leaves `turn` unchanged, so the building stays at the same angle.

Each orbit has its own `offset` and `reverse`, stored in `frames/orbits.json`. They are found by `tools/prepare_orbits.py`,
which compares the frame sequences: each frame gets a column profile (where the vertical edges are: building
corners, towers). It barely depends on camera tilt. For every shift, the similarity of all frames at once is computed,
and the best shift wins. If orbits have different frame counts (360 and 180), comparison is done by fraction of a revolution.

## Preparing frames

```
pip install pillow numpy
python3 tools/prepare_orbits.py --out frames \
  --orbit 1="…/CC_Aerial_Selecter_panorama360_001::CC_Pano360_001_*.jpg" \
  --orbit 2="…/CC_Aerial_Selecter_panorama360_002" \
  --orbit 3="…/CC_Aerial_Selecter_panorama360_003_02" \
  --reference 2 --size 1600
```

For the web (fewer files): `--size 800 --quality 68 --pack`.

The script prints the offsets it found and its confidence, and writes `frames/calibration.jpg` next to them: the same angles
(0°, 45°, …) on every orbit. If a row looks shifted, set the shift by hand: `--offset 3=12`, `--reverse 3`.

If a folder holds several series (masks, RenderElements), the script takes the largest one and reports which it skipped;
a mask after `::` picks one explicitly.

## Running locally

Any local server works (browsers will not load frames from `file://`):

```
python3 -m http.server 8000      # then open http://localhost:8000
```

URL parameters: `?debug` — panel with all three orbits at the current angle, `?orbit=1`, `?deg=90`, `?src=path/`.

In debug mode (`D`): `[` and `]` shift the current orbit by one frame; the frame number updates immediately. Copy the value
to `orbits.json` → `offset`. The same file has `shiftY` and `zoom`, for when the building sits at different heights
in the frame on different orbits.

Control settings (`orbits.json` → `controls`): `degPerPx` (degrees per pixel), `dragSign` (direction),
`switchPx` (vertical pixels per step), `hysteresis`.

## Deployment

GitHub Pages serves the `main` branch root (`.nojekyll` disables Jekyll processing). Every push to `main` redeploys.
