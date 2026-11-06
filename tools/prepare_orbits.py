#!/usr/bin/env python3
"""
Подготовка кадров трёх орбит для прототипа (index.html рядом с orbits.json).

Что делает:
  1. Берёт кадры каждой орбиты из своей папки (только верхний уровень, по номеру в конце имени файла).
  2. Ужимает их в webp для веба (--size по длинной стороне, --crop W:H — обрезка по центру).
  3. Совмещает орбиты по углу: для каждой орбиты ищет, какой её кадр соответствует кадру 0 опорной орбиты
     (offset) и не крутится ли она в обратную сторону (reverse). Пишет это в orbits.json.
  4. Кладёт рядом calibration.jpg — листы «одни и те же углы на всех орбитах», чтобы проверить глазами.

Как ищется совпадение. Наклон камеры меняет картинку по вертикали, но порядок того, что стоит слева направо
(углы дома, башни, деревья), от наклона почти не зависит. Поэтому у каждого кадра берётся «профиль по
столбцам» — сколько вертикальных границ в каждом столбце уменьшенного кадра. Затем сравниваются целые
последовательности: для каждого сдвига s и направления считается средняя похожесть всех 360 пар кадров
«опорная[i] ↔ орбита[s ± i]». Голосуют все кадры сразу, поэтому один неудачный ракурс ничего не ломает.
Число кадров у орбит может различаться (например 360 и 180) — сравнение идёт по доле оборота.

Пример (папки из Dropbox WebApp):
  python3 prepare_orbits.py --out ../frames \
      --orbit 1="…/CC_Aerial_Selecter_panorama360_001" \
      --orbit 2="…/CC_Aerial_Selecter_panorama360_002" \
      --orbit 3="…/CC_Aerial_Selecter_panorama360_003_02" \
      --reference 2 --size 1600

  Если в папке лежат и другие файлы (маски, проходы), сузьте выбор: --orbit 1="папка::CC_Pano360_001_*.jpg"
  Совмещение можно перебить руками: --offset 3=12 --reverse 3

Нужны: python3, Pillow, numpy (pip install pillow numpy).
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
        sys.exit(f'В {folder} нет кадров{" по маске " + pattern if pattern else ""}')
    # Если в папке несколько серий (разные префиксы) — берём самую большую: это обычно бьюти.
    series = {}
    for f in files:
        series.setdefault(re.sub(r'\d+(?=\.[^.]+$)', '#', f), []).append(f)
    key = max(series, key=lambda k: len(series[k]))
    if len(series) > 1:
        print(f'  {folder}: несколько серий, беру «{key}» ({len(series[key])} шт.); '
              f'другие: {", ".join(k for k in series if k != key)}')
    files = sorted(series[key], key=frame_number)
    nums = [frame_number(f) for f in files]
    gaps = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
    if gaps:
        print(f'  ВНИМАНИЕ: пропущены номера {gaps[:20]}{" …" if len(gaps) > 20 else ""}')
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
    """Профиль по столбцам: энергия вертикальных границ, 96 столбцов, нормирован (среднее 0, длина 1)."""
    g = np.asarray(img.convert('L').resize((96, 72), Image.BILINEAR), dtype=np.float32)
    gx = np.abs(np.diff(g, axis=1, append=g[:, -1:]))      # горизонтальный градиент = вертикальные границы
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
    print(f'  орбита {oid}: {len(files)} кадров ({files[0]} … {files[-1]}) → {dst}')
    return {'files': files, 'desc': np.stack([r[0] for r in res]), 'size': res[0][1]}


def best_alignment(ref, other):
    """Сдвиг (в кадрах другой орбиты) и направление, при которых последовательности совпадают лучше всего."""
    A, B = ref['desc'], other['desc']
    nA, nB = len(A), len(B)
    S = A @ B.T                                          # похожесть каждого кадра A с каждым кадром B
    j = np.round(np.arange(nA) * nB / nA).astype(int)    # кадр B на той же доле оборота (без сдвига)
    scores = {}
    for d in (1, -1):
        idx = (d * j[None, :] + np.arange(nB)[:, None]) % nB   # [сдвиг, i]
        scores[d] = S[np.arange(nA)[None, :], idx].mean(axis=1)
    d = 1 if scores[1].max() >= scores[-1].max() else -1
    s = int(scores[d].argmax())
    sc = scores[d].copy()
    # уверенность: насколько лучший сдвиг выше лучшего «далёкого» (не соседнего) сдвига
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
    ap.add_argument('--orbit', action='append', required=True, metavar='ID=ПАПКА[::маска]',
                    help='орбита: номер и папка с кадрами; порядок номеров = сверху вниз (1 — верхний свайп)')
    ap.add_argument('--out', required=True, help='куда положить orbitN/0000.webp … и orbits.json')
    ap.add_argument('--reference', help='орбита, с которой совмещаются остальные (по умолчанию — средняя)')
    ap.add_argument('--start', help='с какой орбиты прототип открывается (по умолчанию — опорная)')
    ap.add_argument('--start-frame', type=int, default=0, help='кадр опорной орбиты, с которого открывается')
    ap.add_argument('--size', type=int, default=1600, help='длинная сторона кадра, px (0 — не уменьшать)')
    ap.add_argument('--crop', help='обрезать по центру до пропорции, например 4:3')
    ap.add_argument('--quality', type=int, default=82)
    ap.add_argument('--offset', action='append', default=[], metavar='ID=N', help='задать сдвиг руками')
    ap.add_argument('--reverse', action='append', default=[], metavar='ID', help='орбита крутится в обратную сторону')
    ap.add_argument('--no-align', action='store_true', help='не искать сдвиг (все offset = 0)')
    ap.add_argument('--pack', action='store_true',
                    help='дополнительно сложить кадры каждой орбиты в файлы orbitN/pack_*.bin (меньше запросов)')
    ap.add_argument('--pack-mb', type=float, default=15, help='предельный размер одного файла упаковки, МБ')
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

    print('Кадры:')
    orbits = {oid: process_orbit(oid, specs[oid], a.out, a.size, aspect, a.quality, a.jobs) for oid in ids}

    print(f'Совмещение (опорная орбита {ref_id}):')
    manifest = {'version': 1, 'reference': ref_id, 'start': {'orbit': a.start or ref_id, 'frame': a.start_frame},
                'orbits': []}
    for oid in ids:
        o = orbits[oid]
        if oid == ref_id or a.no_align:
            off, rev, note = 0, False, 'опорная' if oid == ref_id else 'без совмещения'
        else:
            off, rev, score, margin = best_alignment(orbits[ref_id], o)
            note = f'похожесть {score:.3f}, отрыв от других сдвигов {margin:.3f}'
            if margin < 0.02:
                note += '  ← НЕУВЕРЕННО, проверьте calibration.jpg и задайте --offset руками'
        if oid in manual_off:
            off, note = manual_off[oid] % len(o['files']), 'задано руками'
        if oid in manual_rev:
            rev = True
        print(f'  орбита {oid}: offset {off} (исходный файл {o["files"][off]}), '
              f'{"обратное направление, " if rev else ""}{note}')
        manifest['orbits'].append({
            'id': oid, 'dir': f'orbit{oid}/', 'count': len(o['files']), 'pad': 4, 'ext': 'webp',
            'offset': off, 'reverse': rev, 'width': o['size'][0], 'height': o['size'][1],
            'shiftY': 0, 'zoom': 1, 'source': [o['files'][0], o['files'][-1]],
        })
    if a.pack:
        # Кадры через один по файлам (k % P): после первого файла уже есть весь круг с крупным шагом.
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
            print(f'  орбита {m["id"]}: {P} файл(ов) упаковки')
    # кадр старта в опорной орбите -> доля оборота
    ref = next(m for m in manifest['orbits'] if m['id'] == ref_id)
    manifest['start']['turn'] = a.start_frame / ref['count']
    with open(os.path.join(a.out, 'orbits.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    contact_sheet(a.out, orbits, manifest, ref_id, os.path.join(a.out, 'calibration.jpg'))
    print(f'Готово: {os.path.join(a.out, "orbits.json")}, проверка глазами — {os.path.join(a.out, "calibration.jpg")}')


if __name__ == '__main__':
    main()
