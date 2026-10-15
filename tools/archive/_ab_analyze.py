# -*- coding: utf-8 -*-
"""A/B 对照试验分析：RUN-A 带系留绳 vs RUN-B 拆绳。
文件指针：logs/_ab_a.txt / logs/_ab_b.txt 各自记录 ident_latest.txt 的快照。
"""
import csv
import os

LOG = r'G:\playground\isaac_flapper_sim\logs'


def load(tag):
    with open(os.path.join(LOG, tag), encoding='utf-8') as f:
        fn = f.read().strip()
    with open(os.path.join(LOG, fn), encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return fn, rows


def c(rows, n):
    return [float(r[n]) for r in rows]


def report(tag, label):
    fn, rows = load(tag)
    t = c(rows, 't')
    t1 = t[-1]
    z = c(rows, 'z')
    fq = c(rows, 'freq')
    pit = c(rows, 'pitch')
    rol = c(rows, 'roll')
    vx = c(rows, 'vx')
    vy = c(rows, 'vy')
    ten = c(rows, 'tension')
    print('=' * 72)
    print('[%s] file=%s rows=%d t=%.2f..%.2fs' % (label, fn, len(rows), t[0], t1))
    for name, v in (('freq', fq), ('z', z), ('roll', rol), ('pitch', pit),
                    ('vx', vx), ('vy', vy), ('tension', ten)):
        tail = [x for x, tt in zip(v, t) if tt >= t1 - 15]
        print('  %-8s full[%8.3f,%8.3f] last15s mean=%8.3f min=%8.3f max=%8.3f'
              % (name, min(v), max(v), sum(tail) / len(tail), min(tail), max(tail)))
    g = [tt for tt, x in zip(t, z) if x < 0.2]
    bad = [tt for tt, x, y in zip(t, pit, rol) if max(abs(x), abs(y)) > 1.0]
    tailz = [x for x, tt in zip(z, t) if tt >= t1 - 15]
    print('  grounded(z<0.2) first at: %s' % (('%6.2fs' % g[0]) if g else ' NEVER'))
    print('  |pitch|or|roll|>1rad first at: %s' % (('%6.2fs' % bad[0]) if bad else ' NEVER'))
    ok = all(abs(x - 1.5) <= 0.15 for x in tailz)
    print('  last15s z mean=%.3f  hover-spec(1.5+/-0.15): %s'
          % (sum(tailz) / len(tailz), 'PASS' if ok else 'FAIL'))
    print('  release window 0.85..2.2s:')
    for r in rows:
        tt = float(r['t'])
        if 0.85 <= tt <= 2.2 and abs(tt * 10 - round(tt * 10)) < 0.6:
            print('    t=%5.2f f=%5.2f z=%6.3f roll=%6.3f pitch=%6.3f wy=%6.2f ten=%6.1f'
                  % (tt, float(r['freq']), float(r['z']), float(r['roll']),
                     float(r['pitch']), float(r['wy']), float(r['tension'])))


report('_ab_a.txt', 'RUN-A tether=ON ')
report('_ab_b.txt', 'RUN-B tether=OFF')
