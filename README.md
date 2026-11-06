# Vernadsky — three orbits (prototype)

Live preview: https://nikita-gorban.github.io/vernadsky-proto/

A fly-around of the building at three heights: swipe left-right to rotate, up-down to switch orbit; the angle stays the same.

Each frame comes in two tiers (AVIF), in `frames/orbitN/`:

- `spin/` — 800 px, quality 50, about 40–75 KB a frame (61 MB for all three orbits). Used while the view moves.
  Loaded coarse to fine: a few frames around the current angle on every orbit, then the current orbit every
  24°, 8°, 4°, 2°, 1°, then the other orbits. The full circle can be turned within seconds; until a frame arrives,
  the nearest loaded one is shown.
- `sharp/` — full resolution, quality 65: 1920 px for orbits 1 and 3, 1280 px for orbit 2 (its beauty pass only
  exists at 1280). About 150–550 KB a frame. Fetched only once the view rests on a frame for 150 ms, for the current
  orbit and the same angle on its neighbours, then cross-faded in. At most 40 are kept in memory.

Settings were picked on test frames: AVIF is 20–30% smaller than WebP at the same SSIM; at q55 the sharp tier
smears the tower windows, at q65 it is close to the source.

## Sun mode (time of day)

The sun button under the height switcher turns the view to orbit 1, 0° — the camera of the sun study
(`WebApp/CC_Sun`, 101 frames from morning to night, 1280 px) — and shows a time slider at the bottom. The study exists
for this one view only, so turning the view or changing the height leaves the mode. In the mode, ← → step the time.

Frames are in `frames/sun/{spin,sharp}/` (spin 4.5 MB in all), loaded after the orbits, or first once the mode is on.
The slider opens at the frame whose light is closest to the orbit renders (63), so entering the mode does not jump.
Prepared with:

```
python3 tools/prepare_sun.py --out frames --orbit 1 --frame 0 \
  --src "…/WebApp/CC_Sun::CC_Sun_[0-9]*.jpg"
```

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
  --orbit 3="…/CC_Aerial_Selecter_panorama360_003_02::CC_Panorama360_003_[0-9]*.jpg" \
  --orbit 1="…/CC_Aerial_Selecter_panorama360_001::CC_Pano360_001_[0-9]*.jpg" \
  --orbit 2="…/CC_Aerial_Selecter_panorama360_002::CC_Panorama360_002[0-9]*.jpg" \
  --reference 2 --start 1
```

Sources live in Dropbox: `2026/Vernadsky/02_Output/Result/WebApp/`. The masks matter: the folders also hold render
passes (ZDepth, WireColor, …) with the same frame count. Tier settings: `--spin-size`, `--spin-quality`,
`--sharp-size`, `--sharp-quality`, `--format avif|webp`. Needs Pillow 11.3+ for AVIF. Takes about 2.5 minutes.

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
`switchPx` (vertical pixels per step), `hysteresis`, `parallel` (concurrent spin downloads),
`sharpDelayMs`, `sharpCache`, `fadeMs`.

Browser support: AVIF needs Chrome 85+, Firefox 93+, Safari 16.4+.

## Deployment

GitHub Pages serves the `main` branch root (`.nojekyll` disables Jekyll processing). Every push to `main` redeploys.
