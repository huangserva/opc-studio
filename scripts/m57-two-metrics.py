#!/usr/bin/env python3
"""M57-二：同一份输出，两个口径并排量（样本级 = 判定线；5ms 包络 = 诊断）。

用法:
  python3 scripts/m57-two-metrics.py --truth <真值.mp4> --cand <待评.mp4> [--cand ...] \
      --music <音乐.mp3> --volume 0.3585397 --t0 14.20 --t1 14.60 [--label ...]

有 --music 时先减掉音乐（plain），只比「声响」那部分；没有就直接比整段。
样本级：零延迟相关 + ±60ms 内最佳相关与最佳延迟（1 样本精度）。
包络：5ms RMS 包络，±60ms 内最佳相关与延迟（5ms 精度）。
"""
import argparse
import subprocess
import sys

import numpy as np

SR = 44100


def load(path, t0=None, t1=None):
    cmd = ["ffmpeg", "-v", "error"]
    if t0 is not None:
        cmd += ["-ss", str(t0), "-to", str(t1)]
    cmd += ["-i", path, "-map", "a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if not r.stdout:
        sys.exit(f"读不到音频: {path} {r.stderr[-200:].decode()}")
    return np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)


def corr(a, b):
    if len(a) < 8 or a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def env5(x):
    n = int(0.005 * SR)
    m = len(x) // n
    return np.sqrt((x[:m * n].reshape(m, n) ** 2).mean(axis=1))


def sample_metrics(ref, cand, max_ms=60):
    m = min(len(ref), len(cand))
    ref, cand = ref[:m], cand[:m]
    z = corr(ref, cand)
    best = (z, 0)
    step = max(1, int(SR * 0.001))
    for lag in range(-int(SR * max_ms / 1000), int(SR * max_ms / 1000) + 1, step):
        c = corr(ref[lag:], cand[:m - lag]) if lag >= 0 else corr(ref[:m + lag], cand[-lag:])
        if c > best[0]:
            best = (c, lag)
    lo, hi = best[1] - step, best[1] + step
    for lag in range(lo, hi + 1):
        c = corr(ref[lag:], cand[:m - lag]) if lag >= 0 else corr(ref[:m + lag], cand[-lag:])
        if c > best[0]:
            best = (c, lag)
    return z, best[0], best[1] / SR * 1000.0


def env_metrics(ref, cand, max_ms=60):
    e1, e2 = env5(ref), env5(cand)
    m = min(len(e1), len(e2))
    e1, e2 = e1[:m], e2[:m]
    best = (corr(e1, e2), 0)
    for lag in range(-max_ms // 5, max_ms // 5 + 1):
        c = corr(e1[lag:], e2[:m - lag]) if lag >= 0 else corr(e1[:m + lag], e2[-lag:])
        if c > best[0]:
            best = (c, lag)
    return best[0], best[1] * 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--cand", action="append", required=True)
    ap.add_argument("--label", action="append", default=[])
    ap.add_argument("--music")
    ap.add_argument("--volume", type=float, default=1.0)
    ap.add_argument("--t0", type=float, required=True)
    ap.add_argument("--t1", type=float, required=True)
    a = ap.parse_args()
    n0, n1 = int(a.t0 * SR), int(a.t1 * SR)
    truth = load(a.truth)[n0:n1]
    if a.music:
        truth = truth - load(a.music)[n0:n1] * a.volume
    labels = a.label + [f"候选{i+1}" for i in range(len(a.cand) - len(a.label))]
    print(f"窗口 {a.t0}-{a.t1}s；{'已减音乐(plain)' if a.music else '未减音乐'}；"
          f"参考 rms={np.sqrt((truth**2).mean()):.4f}")
    print(f"{'待评':<26}{'样本@0':>9}{'样本最佳':>10}{'样本延迟ms':>11}{'包络最佳':>10}{'包络延迟ms':>11}{'rms比':>8}")
    for lab, c in zip(labels, a.cand):
        x = load(c)[n0:n1]
        if a.music:
            x = x - load(a.music)[n0:n1] * a.volume
        m = min(len(truth), len(x))
        z, b, bl = sample_metrics(truth[:m], x[:m])
        e, el = env_metrics(truth[:m], x[:m])
        rr = float(np.sqrt((x[:m] ** 2).mean()) / max(np.sqrt((truth[:m] ** 2).mean()), 1e-12))
        print(f"{lab:<26}{z:>9.4f}{b:>10.4f}{bl:>11.2f}{e:>10.4f}{el:>11.0f}{rr:>8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
