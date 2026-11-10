#!/usr/bin/env python3
"""level_check 합성 데이터 테스트. 실행: python3 test_level_check.py  (ffmpeg 필요, 1~2분)
실제 Xinsight 오디오가 아니라 유사 음성(잡음+음절 변조+쉼)이다. 구조 검증용."""
import json, os, re, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import level_check as lc

SR = lc.SR
rng = np.random.default_rng(0)

def speech(dur, level_db, pause_scale=1.0):
    out, t = [], 0.0
    while t < dur:
        sl = min(rng.uniform(2.0, 5.0), dur - t); n = int(sl * SR)
        x = rng.standard_normal(n); tt = np.arange(n) / SR
        x *= 0.55 + 0.45 * np.sin(2 * np.pi * rng.uniform(3, 5) * tt + rng.uniform(0, 6.28))
        x *= 10 ** (level_db / 20) / np.sqrt(np.mean(x ** 2)); out.append(x); t += sl
        if t < dur:
            pl = min(rng.uniform(0.3, 0.9) * pause_scale, dur - t); out.append(np.zeros(int(pl * SR))); t += pl
    return np.concatenate(out)

def episode(gap_db, mean_turn_s, total_s=300.0, base_db=-23.0, tail_silence=0.0, head_silence=0.0):
    segs, tl, t, spk = [], [], 0.0, 0
    if head_silence:
        segs.append(np.zeros(int(head_silence * SR))); t += head_silence
    while t < total_s - 0.01:
        d = min(max(rng.exponential(mean_turn_s), 1.5), total_s - t)
        s = speech(d, base_db + (gap_db if spk else 0.0))
        d = len(s) / SR
        segs.append(s); tl.append({"speaker": "B" if spk else "A", "start": t, "end": t + d}); t += d
        if t < total_s - 0.01:
            segs.append(np.zeros(int(0.35 * SR))); t += 0.35
        spk ^= 1
    if tail_silence:
        segs.append(np.zeros(int(tail_silence * SR)))
    return np.concatenate(segs), tl

def write_wav(x, path):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    pcm.tofile(path + ".raw")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", path + ".raw", path], check=True)
    os.remove(path + ".raw")

def ffmpeg_mean_volume(path, start, end):
    o = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", f"atrim=start={start}:end={end},volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: ([-\d.]+) dB", o).group(1))

def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    return cond

def main():
    ok = True
    tmp = tempfile.mkdtemp()

    # 1. legacy_db 가 ffmpeg volumedetect 와 일치하는가 (창 4개)
    globals()["rng"] = np.random.default_rng(7)
    x, tl = episode(5.0, 10.0, total_s=75.0)
    p = os.path.join(tmp, "a.wav"); write_wav(x, p)
    xd, _ = lc.decode(p)
    rep = lc.measure(xd, tl)
    diffs = [abs(w["legacy_db"] - ffmpeg_mean_volume(p, w["start"], w["end"])) for w in rep["windows"][:4]]
    ok &= check("legacy_db == ffmpeg volumedetect (±0.15 dB)", max(diffs) < 0.15, f"max diff={max(diffs):.3f}")

    # 2. 간격 5 dB + 긴 턴: legacy 는 실패, neutral 은 통과
    globals()["rng"] = np.random.default_rng(1)
    x, tl = episode(5.0, 12.0, total_s=300.0)
    rep = lc.measure(x[:, None], tl)
    ok &= check("gap5/long turns: legacy_dev > 3.5", rep["legacy_dev"] > 3.5, f"legacy={rep['legacy_dev']:.2f}")
    ok &= check("gap5/long turns: neutral_dev <= 2.0", rep["neutral_dev"] <= 2.0, f"neutral={rep['neutral_dev']:.2f}")
    offs = rep["speaker_offsets_db"]
    ok &= check("auto offsets recover the 5 dB gap (±0.5)", abs((offs["B"] - offs["A"]) - 5.0) < 0.5, f"B-A={offs['B']-offs['A']:.2f}")

    # 3. 꼬리 무음: legacy 는 튀고 gated 는 안정
    globals()["rng"] = np.random.default_rng(2)
    x0, tl = episode(0.0, 10.0, total_s=305.0)
    x1 = np.concatenate([x0, np.zeros(int(6 * SR))])
    r0 = lc.measure(x0[:, None], tl); r1 = lc.measure(x1[:, None], tl, min_last_s=0.0)  # 합치기 끔
    ok &= check("tail silence raises legacy_dev by >1.5 dB", r1["legacy_dev"] - r0["legacy_dev"] > 1.5,
                f"{r0['legacy_dev']:.2f} -> {r1['legacy_dev']:.2f}")
    ok &= check("tail silence leaves gated_dev within 0.3 dB", abs(r1["gated_dev"] - r0["gated_dev"]) < 0.3,
                f"{r0['gated_dev']:.2f} -> {r1['gated_dev']:.2f}")

    # 4. 짧은 마지막 창 합치기
    r2 = lc.measure(x1[:, None], tl)  # 기본 min_last 7.5: 마지막 11 s 창은 합치지 않음(>7.5)
    x3 = np.concatenate([x0[: int(302 * SR)], np.zeros(int(3 * SR))])  # 마지막 창 5 s
    r3 = lc.measure(x3[:, None], tl)
    ok &= check("short last window merged", r3["windows"][-1]["merged"] and len(r3["windows"]) == 20,
                f"n={len(r3['windows'])} last=({r3['windows'][-1]['start']:.1f},{r3['windows'][-1]['end']:.1f})")

    # 5. 예측기: 타임라인+화자 음량 → legacy 예측이 실측과 1 dB 이내
    globals()["rng"] = np.random.default_rng(3)
    x, tl = episode(5.0, 8.0, total_s=300.0)
    rep = lc.measure(x[:, None], tl)
    lv = {"A": -23.0 + 10 * np.log10(0.82), "B": -18.0 + 10 * np.log10(0.82)}  # 쉼 포함 턴 평균 추정(발화 82%)
    pr = lc.predict(tl, lv)
    ok &= check("predicted legacy_dev within 1.0 dB of measured", abs(pr["predicted_legacy_dev"] - rep["legacy_dev"]) < 1.0,
                f"pred={pr['predicted_legacy_dev']:.2f} meas={rep['legacy_dev']:.2f}")
    ok &= check("predicted neutral_dev == 0 (ideal)", pr["predicted_neutral_dev"] < 1e-6)

    # 6. 대본 기반 타임라인 추정이 동작
    tl2 = lc.timeline_from_script([{"speaker": "서연", "text": "안녕하세요 " * 10}, {"speaker": "지훈", "text": "네 " * 5}])
    ok &= check("timeline_from_script", len(tl2) == 2 and tl2[1]["start"] > tl2[0]["end"])

    # 7. CLI 왕복 (JSON 저장)
    tlp = os.path.join(tmp, "tl.json"); json.dump(tl, open(tlp, "w"))
    p = os.path.join(tmp, "b.wav"); write_wav(x, p)
    rc = subprocess.run([sys.executable, lc.__file__, p, "--timeline", tlp, "--json", os.path.join(tmp, "r.json")],
                        capture_output=True, text=True)
    ok &= check("CLI runs and writes JSON", os.path.exists(os.path.join(tmp, "r.json")), f"rc={rc.returncode}")
    print(rc.stdout[-600:])
    print("ALL OK" if ok else "SOME FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
