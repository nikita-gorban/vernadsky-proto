#!/usr/bin/env python3
"""
Prepares frames of the three orbits for the prototype (index.html next to orbits.json).

What it does:
  1. Takes each orbit's frames from its own folder (top level only, ordered by the number at the end of the name).
  2. Compresses them to webp for the web (--size on the long side, --crop W:H for a centered crop).
  3. Aligns orbits by angle: for each orbit, finds which of its frames matches frame 0 of the reference orbit
     (offset) and whether it spins the other way (reverse). Writes this to orbits.json.
  4. Writes calibration.jpg next to it: the same angles on every orbit, for a visual check.

How matching works. Camera tilt changes the image vertically, but the left-to-right order of things
(building corners, towers, trees) barely depends on tilt. So each frame gets a "column profile": how many
vertical edges each column of a downscaled frame has. Then whole sequences are compared: for every shift s and
direction, the mean similarity of all 360 pairs "reference[i] <-> orbit[s ± i]" is computed. All frames vote at
once, so one bad view breaks nothing. Orbits may have different frame counts (e.g. 360 and 180): comparison is
done by fraction of a revolution.

Example (folders from Dropbox WebApp):
  python3 prepare_orbits.py --out ../frames \
      --orbit 1="…/CC_Aerial_Selecter_panorama360_001" \
      --orbit 2="…/CC_Aerial_Selecter_panorama360_002" \
      --orbit 3="…/CC_Aerial_Selecter_panorama360_003_02" \
      --reference 2 --size 1600

  If the folder holds other files too (masks, passes), narrow the selection: --orbit 1="folder::CC_Pano360_001_*.jpg"
  Alignment can be overridden by hand: --offset 3=12 --reverse 3

Requires: python3, Pillow, numpy (pip install pillow numpy).
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


def process_orbit(oid, spec, out_dir, size, aspect, quality, jobs):
    folder, files = list_frames(spec)
    dst = os.path.join(out_dir, f'orbit{oid}')
    os.makedirs(dst, exist_ok=True)

    def one(k):
        src = os.path.join(folder, files[k])
        with Image.open(src) as im:
            im = im.convert('RGB')
            im = im.crop(crop_box(im.width, im.height, aspect))
            if size and max(im.size) > size:
                r = size / max(im.size)
                im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
            im.save(os.path.join(dst, f'{k:04d}.webp'), 'WEBP', quality=quality, method=4)
            return descriptor(im), im.size

    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(one, range(len(files))))
    print(f'  orbit {oid}: {len(files)} frames ({files[0]} … {files[-1]}) → {dst}')
    return {'files': files, 'desc': np.stack([r[0] for r in res]), 'size': res[0][1]}


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


def contact_sheet(out_dir, orbits, manifest, ref_id, path):
    ids = list(orbits)
    angles = [0, 45, 90, 135, 180, 225, 270, 315]
    tw = 240
    first = Image.open(os.path.join(out_dir, f'orbit{ids[0]}', '0000.webp'))
    th = round(tw * first.height / first.width)
    sheet = Image.new('RGB', (tw * len(angles), th * len(ids)), 'black')
    for r, oid in enumerate(ids):
        m = next(o for o in manifest['orbits'] if o['id'] == oid)
        for c, a in enumerate(angles):
            t = a / 360
            k = (m['offset'] + (-1 if m['reverse'] else 1) * round(t * m['count'])) % m['count']
            with Image.open(os.path.join(out_dir, f'orbit{oid}', f'{k:04d}.webp')) as im:
                sheet.paste(im.convert('RGB').resize((tw, th)), (c * tw, r * th))
    sheet.save(path, quality=85)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--orbit', action='append', required=True, metavar='ID=FOLDER[::mask]',
                    help='orbit: id and frame folder; argument order = top to bottom (first = highest swipe)')
    ap.add_argument('--out', required=True, help='where to write orbitN/0000.webp … and orbits.json')
    ap.add_argument('--reference', help='orbit the others are aligned to (default: the middle one)')
    ap.add_argument('--start', help='orbit the prototype opens on (default: the reference)')
    ap.add_argument('--start-frame', type=int, default=0, help='reference orbit frame the prototype opens on')
    ap.add_argument('--size', type=int, default=1600, help='long side of a frame, px (0 = no downscale)')
    ap.add_argument('--crop', help='center crop to an aspect ratio, e.g. 4:3')
    ap.add_argument('--quality', type=int, default=82)
    ap.add_argument('--offset', action='append', default=[], metavar='ID=N', help='set the shift by hand')
    ap.add_argument('--reverse', action='append', default=[], metavar='ID', help='orbit spins the other way')
    ap.add_argument('--no-align', action='store_true', help='skip alignment (all offsets = 0)')
    ap.add_argument('--pack', action='store_true',
                    help='also pack each orbit\'s frames into orbitN/pack_*.bin files (fewer requests)')
    ap.add_argument('--pack-mb', type=float, default=15, help='max size of one pack file, MB')
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
    os.makedirs(a.out, exist_ok=True)

    print('Frames:')
    orbits = {oid: process_orbit(oid, specs[oid], a.out, a.size, aspect, a.quality, a.jobs) for oid in ids}

    print(f'Alignment (reference orbit {ref_id}):')
    manifest = {'version': 1, 'reference': ref_id, 'start': {'orbit': a.start or ref_id, 'frame': a.start_frame},
                'orbits': []}
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
        manifest['orbits'].append({
            'id': oid, 'dir': f'orbit{oid}/', 'count': len(o['files']), 'pad': 4, 'ext': 'webp',
            'offset': off, 'reverse': rev, 'width': o['size'][0], 'height': o['size'][1],
            'shiftY': 0, 'zoom': 1, 'source': [o['files'][0], o['files'][-1]],
        })
    if a.pack:
        # Frames interleaved across files (k % P): after the first file the full circle is there at a coarse step.
        for m in manifest['orbits']:
            d = os.path.join(a.out, m['dir'])
            blobs = [open(os.path.join(d, f'{k:04d}.webp'), 'rb').read() for k in range(m['count'])]
            P = max(1, -(-sum(map(len, blobs)) // int(a.pack_mb * 1e6)))
            files, index, offs = [], [None] * m['count'], [0] * P
            outs = [open(os.path.join(d, f'pack_{p}.bin'), 'wb') for p in range(P)]
            for k, data in enumerate(blobs):
                p = k % P
                outs[p].write(data); index[k] = [p, offs[p], len(data)]; offs[p] += len(data)
            for f in outs: f.close()
            m['pack'], m['index'] = [f'pack_{p}.bin' for p in range(P)], index
            print(f'  orbit {m["id"]}: {P} pack file(s)')
    # start frame in the reference orbit -> fraction of a revolution
    ref = next(m for m in manifest['orbits'] if m['id'] == ref_id)
    manifest['start']['turn'] = a.start_frame / ref['count']
    with open(os.path.join(a.out, 'orbits.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    contact_sheet(a.out, orbits, manifest, ref_id, os.path.join(a.out, 'calibration.jpg'))
    print(f'Done: {os.path.join(a.out, "orbits.json")}, visual check: {os.path.join(a.out, "calibration.jpg")}')


if __name__ == '__main__':
    main()
