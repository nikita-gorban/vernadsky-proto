#!/usr/bin/env python3
"""
Prepares frames of the three orbits for the prototype (index.html next to orbits.json).

What it does:
  1. Takes each orbit's frames from its own folder (top level only, ordered by the number at the end of the name).
  2. Encodes two tiers per frame (--crop W:H for a centered crop first):
       spin/  — small and light, used while the view is moving; the viewer loads all of them, coarse to fine;
       sharp/ — full resolution, loaded only for the frame the view rests on.
  3. Aligns orbits by angle: for each orbit, finds which of its frames matches frame 0 of the reference orbit
     (offset) and whether it spins the other way (reverse). Writes this to orbits.json.
  4. Writes calibration.jpg next to it: the same angles on every orbit, for a visual check.

How matching works. Camera tilt changes the image vertically, but the left-to-right order of things
(building corners, towers, trees) barely depends on tilt. So each frame gets a "column profile": how many
vertical edges each column of a downscaled frame has. Then whole sequences are compared: for every shift s and
direction, the mean similarity of all 360 pairs "reference[i] <-> orbit[s ± i]" is computed. All frames vote at
once, so one bad view breaks nothing. Orbits may have different frame counts (e.g. 360 and 180): comparison is
done by fraction of a revolution.

Example (folders from Dropbox WebApp; argument order = orbit order top to bottom):
  python3 tools/prepare_orbits.py --out frames \\
      --orbit 3="…/CC_Aerial_Selecter_panorama360_003_02::CC_Panorama360_003_[0-9]*.jpg" \\
      --orbit 1="…/CC_Aerial_Selecter_panorama360_001::CC_Pano360_001_[0-9]*.jpg" \\
      --orbit 2="…/CC_Aerial_Selecter_panorama360_002::CC_Panorama360_002[0-9]*.jpg" \\
      --reference 2 --start 1

  If the folder holds other files too (masks, passes), narrow the selection with a mask after "::".
  Alignment can be overridden by hand: --offset 3=12 --reverse 3
  Re-running keeps "controls", "note", "sun" (see prepare_sun.py), and per-orbit "shiftY"/"zoom" from an existing
  orbits.json.

Requires: python3, Pillow 11.3+ (built-in AVIF), numpy (pip install pillow numpy).
"""
import argparse, fnmatch, json, os, re, sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.exr')


def frame_number(name):
    m = re.search(r'(\d+)(?=\.[^.]+$)', name)
    return int(m.group(1)) if m else None


def list_frames(spec):
    folder, _, pattern = spec.partition('::')
    files = [f for f in os.listdir(folder)
             if f.lower().endswith(EXTS) and os.path.isfile(os.path.join(folder, f))
             and (not pattern or fnmatch.fnmatch(f, pattern))]
    files = [f for f in files if frame_number(f) is not None]
    if not files:
        sys.exit(f'No frames in {folder}{" matching " + pattern if pattern else ""}')
    # If the folder holds several series (different prefixes), take the largest: usually the beauty pass.
    series = {}
    for f in files:
        series.setdefault(re.sub(r'\d+(?=\.[^.]+$)', '#', f), []).append(f)
    key = max(series, key=lambda k: len(series[k]))
    if len(series) > 1:
        print(f'  {folder}: several series, using "{key}" ({len(series[key])} files); '
              f'others: {", ".join(k for k in series if k != key)}')
    files = sorted(series[key], key=frame_number)
    nums = [frame_number(f) for f in files]
    gaps = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
    if gaps:
        print(f'  WARNING: missing frame numbers {gaps[:20]}{" …" if len(gaps) > 20 else ""}')
    return folder, files


def crop_box(w, h, aspect):
    if not aspect:
        return (0, 0, w, h)
    if w / h > aspect:
        nw = round(h * aspect)
        return ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    nh = round(w / aspect)
    return (0, (h - nh) // 2, w, (h - nh) // 2 + nh)


def descriptor(img):
    """Column profile: vertical edge energy, 96 columns, normalized (zero mean, unit length)."""
    g = np.asarray(img.convert('L').resize((96, 72), Image.BILINEAR), dtype=np.float32)
    gx = np.abs(np.diff(g, axis=1, append=g[:, -1:]))      # horizontal gradient = vertical edges
    prof = gx.mean(axis=0) + 0.5 * np.abs(np.diff(g.mean(axis=0), append=g.mean(axis=0)[-1]))
    prof = np.convolve(prof, np.ones(3) / 3, mode='same')
    prof -= prof.mean()
    n = np.linalg.norm(prof)
    return prof / n if n else prof


def fit(im, size):
    """Downscale so the long side is at most size (0 = keep). Never upscales."""
    if size and max(im.size) > size:
        r = size / max(im.size)
        im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    return im


def save(im, path, fmt, quality):
    if fmt == 'avif':
        im.save(path, 'AVIF', quality=quality, speed=6)
    else:
        im.save(path, 'WEBP', quality=quality, method=6)


def process_orbit(oid, spec, out_dir, aspect, tiers, fmt, jobs):
    folder, files = list_frames(spec)
    dst = os.path.join(out_dir, f'orbit{oid}')
    for name in tiers:
        os.makedirs(os.path.join(dst, name), exist_ok=True)

    def one(k):
        with Image.open(os.path.join(folder, files[k])) as im:
            im = im.convert('RGB')
            im = im.crop(crop_box(im.width, im.height, aspect))
            sizes = {}
            for name, (size, quality) in tiers.items():
                t = fit(im, size)
                save(t, os.path.join(dst, name, f'{k:04d}.{fmt}'), fmt, quality)
                sizes[name] = t.size
                if name == 'spin':
                    desc = descriptor(t)
            return desc, sizes

    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(one, range(len(files))))
    sizes = res[0][1]
    print(f'  orbit {oid}: {len(files)} frames ({files[0]} … {files[-1]}) → {dst} '
          f'({", ".join(f"{n} {w}px" for n, (w, h) in sizes.items())})')
    return {'files': files, 'desc': np.stack([r[0] for r in res]), 'sizes': sizes}


def best_alignment(ref, other):
    """Shift (in frames of the other orbit) and direction at which the sequences match best."""
    A, B = ref['desc'], other['desc']
    nA, nB = len(A), len(B)
    S = A @ B.T                                          # similarity of every frame of A with every frame of B
    j = np.round(np.arange(nA) * nB / nA).astype(int)    # frame of B at the same fraction of a revolution (no shift)
    scores = {}
    for d in (1, -1):
        idx = (d * j[None, :] + np.arange(nB)[:, None]) % nB   # [shift, i]
        scores[d] = S[np.arange(nA)[None, :], idx].mean(axis=1)
    d = 1 if scores[1].max() >= scores[-1].max() else -1
    s = int(scores[d].argmax())
    sc = scores[d].copy()
    # confidence: how far the best shift beats the best "far" (non-adjacent) shift
    far = [k for k in range(nB) if min((k - s) % nB, (s - k) % nB) > max(2, nB // 36)]
    margin = float(sc[s] - sc[far].max()) if far else 0.0
    return s, d == -1, float(sc[s]), margin


def contact_sheet(out_dir, orbits, manifest, fmt, path):
    ids = list(orbits)
    angles = [0, 45, 90, 135, 180, 225, 270, 315]
    tw = 240
    first = Image.open(os.path.join(out_dir, f'orbit{ids[0]}', 'spin', f'0000.{fmt}'))
    th = round(tw * first.height / first.width)
    sheet = Image.new('RGB', (tw * len(angles), th * len(ids)), 'black')
    for r, oid in enumerate(ids):
        m = next(o for o in manifest['orbits'] if o['id'] == oid)
        for c, a in enumerate(angles):
            t = a / 360
            k = (m['offset'] + (-1 if m['reverse'] else 1) * round(t * m['count'])) % m['count']
            with Image.open(os.path.join(out_dir, f'orbit{oid}', 'spin', f'{k:04d}.{fmt}')) as im:
                sheet.paste(im.convert('RGB').resize((tw, th)), (c * tw, r * th))
    sheet.save(path, quality=85)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--orbit', action='append', required=True, metavar='ID=FOLDER[::mask]',
                    help='orbit: id and frame folder; argument order = top to bottom (first = highest swipe)')
    ap.add_argument('--out', required=True, help='where to write orbitN/{spin,sharp}/0000.avif … and orbits.json')
    ap.add_argument('--reference', help='orbit the others are aligned to (default: the middle one)')
    ap.add_argument('--start', help='orbit the prototype opens on (default: the reference)')
    ap.add_argument('--start-frame', type=int, default=0, help='reference orbit frame the prototype opens on')
    ap.add_argument('--format', choices=('avif', 'webp'), default='avif', help='image format for both tiers')
    ap.add_argument('--spin-size', type=int, default=800, help='long side of a spin frame, px')
    ap.add_argument('--spin-quality', type=int, default=50)
    ap.add_argument('--sharp-size', type=int, default=1920, help='long side of a sharp frame, px (0 = source size)')
    ap.add_argument('--sharp-quality', type=int, default=65)
    ap.add_argument('--crop', help='center crop to an aspect ratio, e.g. 4:3')
    ap.add_argument('--offset', action='append', default=[], metavar='ID=N', help='set the shift by hand')
    ap.add_argument('--reverse', action='append', default=[], metavar='ID', help='orbit spins the other way')
    ap.add_argument('--no-align', action='store_true', help='skip alignment (all offsets = 0)')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    specs = dict(o.split('=', 1) for o in a.orbit)
    ids = list(specs)
    ref_id = a.reference or ids[len(ids) // 2]
    aspect = None
    if a.crop:
        w, h = map(float, a.crop.split(':'))
        aspect = w / h
    manual_off = {k: int(v) for k, v in (o.split('=', 1) for o in a.offset)}
    manual_rev = set(a.reverse)
    tiers = {'spin': (a.spin_size, a.spin_quality), 'sharp': (a.sharp_size, a.sharp_quality)}
    os.makedirs(a.out, exist_ok=True)
    manifest_path = os.path.join(a.out, 'orbits.json')
    old = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding='utf-8') as f:
            old = json.load(f)
    old_orbits = {o['id']: o for o in old.get('orbits', [])}

    print('Frames:')
    orbits = {oid: process_orbit(oid, specs[oid], a.out, aspect, tiers, a.format, a.jobs) for oid in ids}

    print(f'Alignment (reference orbit {ref_id}):')
    manifest = {'version': 2, 'reference': ref_id, 'start': {'orbit': a.start or ref_id, 'frame': a.start_frame},
                'tiers': {name: {'dir': f'{name}/', 'ext': a.format} for name in tiers},
                'orbits': []}
    for key in ('controls', 'note', 'sun'):   # "sun" comes from prepare_sun.py
        if key in old:
            manifest[key] = old[key]
    for oid in ids:
        o = orbits[oid]
        if oid == ref_id or a.no_align:
            off, rev, note = 0, False, 'reference' if oid == ref_id else 'no alignment'
        else:
            off, rev, score, margin = best_alignment(orbits[ref_id], o)
            note = f'similarity {score:.3f}, margin over other shifts {margin:.3f}'
            if margin < 0.02:
                note += '  ← UNCERTAIN, check calibration.jpg and set --offset by hand'
        if oid in manual_off:
            off, note = manual_off[oid] % len(o['files']), 'set by hand'
        if oid in manual_rev:
            rev = True
        print(f'  orbit {oid}: offset {off} (source file {o["files"][off]}), '
              f'{"reversed, " if rev else ""}{note}')
        prev = old_orbits.get(oid, {})
        manifest['orbits'].append({
            'id': oid, 'dir': f'orbit{oid}/', 'count': len(o['files']), 'pad': 4,
            'offset': off, 'reverse': rev,
            'shiftY': prev.get('shiftY', 0), 'zoom': prev.get('zoom', 1),
            'sizes': {name: list(wh) for name, wh in o['sizes'].items()},
            'source': [o['files'][0], o['files'][-1]],
        })
    # start frame in the reference orbit -> fraction of a revolution
    ref = next(m for m in manifest['orbits'] if m['id'] == ref_id)
    manifest['start']['turn'] = a.start_frame / ref['count']
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    contact_sheet(a.out, orbits, manifest, a.format, os.path.join(a.out, 'calibration.jpg'))
    print(f'Done: {manifest_path}, visual check: {os.path.join(a.out, "calibration.jpg")}')


if __name__ == '__main__':
    main()
