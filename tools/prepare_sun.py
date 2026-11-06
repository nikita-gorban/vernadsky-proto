#!/usr/bin/env python3
"""
Prepares the time-of-day sequence for the sun mode: one camera, frames from morning to night.

The camera must match one orbit frame (the sun study was rendered from orbit 1, frame 0). The viewer turns to that
view, then shows the sequence with a time slider. Frames get the same two tiers as the orbits (spin/ and sharp/,
format from orbits.json → tiers) in <out>/sun/, and orbits.json gets a "sun" entry. Run prepare_orbits.py first.

The start frame (the time the slider opens at) is the sequence frame whose shadows match the orbit frame best,
so switching into the sun mode does not visibly jump. Override with --start. --last drops the frames after it
(the study runs into full night; the viewer stops at dusk).

Example:
  python3 tools/prepare_sun.py --out frames --orbit 1 --frame 0 --last 78 --start 62 \\
      --src "…/WebApp/CC_Sun::CC_Sun_[0-9]*.jpg"

Requires: python3, Pillow 11.3+ (built-in AVIF), numpy.
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prepare_orbits import list_frames, fit, save  # noqa: E402


def shadows(path, n=200):
    """Shadow mask of the lower half (the ground): pixels clearly darker than their surroundings."""
    with Image.open(path) as im:
        g = im.convert('L').resize((n, n), Image.BILINEAR).crop((0, n // 2, n, n))
    a = np.asarray(g, dtype=np.float32)
    b = np.asarray(g.filter(ImageFilter.GaussianBlur(6)), dtype=np.float32)
    return a < b * 0.8


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', required=True, metavar='FOLDER[::mask]', help='folder with the sequence frames')
    ap.add_argument('--out', required=True, help='frames folder with orbits.json (from prepare_orbits.py)')
    ap.add_argument('--orbit', required=True, help='orbit id the camera belongs to')
    ap.add_argument('--frame', type=int, required=True, help='frame of that orbit the camera matches')
    ap.add_argument('--start', type=int, help='sequence frame the slider opens at (default: best shadow match)')
    ap.add_argument('--last', type=int, help='last sequence frame to keep, 0-based (default: all)')
    ap.add_argument('--spin-size', type=int, default=800)
    ap.add_argument('--spin-quality', type=int, default=50)
    ap.add_argument('--sharp-size', type=int, default=1920)
    ap.add_argument('--sharp-quality', type=int, default=65)
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    a = ap.parse_args()

    manifest_path = os.path.join(a.out, 'orbits.json')
    with open(manifest_path, encoding='utf-8') as f:
        manifest = json.load(f)
    tiers = manifest['tiers']
    fmt = tiers['spin']['ext']
    orbit = next((o for o in manifest['orbits'] if o['id'] == a.orbit), None)
    if not orbit:
        sys.exit(f'No orbit {a.orbit} in {manifest_path}')

    folder, files = list_frames(a.src)
    if a.last is not None:
        files = files[:a.last + 1]
    dst = os.path.join(a.out, 'sun')
    settings = {'spin': (a.spin_size, a.spin_quality), 'sharp': (a.sharp_size, a.sharp_quality)}
    for name in settings:
        os.makedirs(os.path.join(dst, tiers[name]['dir']), exist_ok=True)

    def one(k):
        with Image.open(os.path.join(folder, files[k])) as im:
            im = im.convert('RGB')
            sizes = {}
            for name, (size, quality) in settings.items():
                t = fit(im, size)
                save(t, os.path.join(dst, tiers[name]['dir'], f'{k:04d}.{fmt}'), fmt, quality)
                sizes[name] = list(t.size)
            return sizes

    with ThreadPoolExecutor(a.jobs) as ex:
        sizes = list(ex.map(one, range(len(files))))[0]
    print(f'Sun: {len(files)} frames ({files[0]} … {files[-1]}) → {dst}')

    start = a.start
    if start is None:
        # Overlap (IoU) of the shadow masks: shadows show the sun's position, unlike overall colour or exposure.
        ref = shadows(os.path.join(a.out, orbit['dir'], tiers['sharp']['dir'], f'{a.frame:04d}.{fmt}'))
        iou = []
        for k in range(len(files)):
            m = shadows(os.path.join(dst, tiers['sharp']['dir'], f'{k:04d}.{fmt}'))
            iou.append((ref & m).sum() / max(1, (ref | m).sum()))
        start = int(np.argmax(iou))
        print(f'  closest to orbit {a.orbit} frame {a.frame}: sequence frame {start} ({files[start]})')

    manifest['sun'] = {'dir': 'sun/', 'count': len(files), 'pad': 4, 'orbit': a.orbit, 'frame': a.frame,
                       'start': start, 'sizes': sizes, 'source': [files[0], files[-1]]}
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f'Done: "sun" entry in {manifest_path}')


if __name__ == '__main__':
    main()
