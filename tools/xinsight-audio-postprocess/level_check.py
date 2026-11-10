#!/usr/bin/env python3
"""Xinsight 15초 구간 음량 편차 측정기 (개선판).

process.py의 검수 단계에서 쓰던 "15초 고정 창 × ffmpeg volumedetect mean_volume 최대−최소"를
의도(음량 일관성)에 맞게 재정의한다. 임계값(기본 3.5 dB)은 바꾸지 않는다.

측정 지표 (모두 한 번에 계산해 JSON으로 낸다)
  legacy_dev      : 기존 방식 그대로. volumedetect mean_volume과 소수점 첫째 자리까지 일치해야 한다.
  gated_dev       : 발화 구간만 평균. 쉼·무음·꼬리 무음·부분 창 영향 제거.
  neutral_dev     : gated + 화자 간 의도된 간격을 되돌린(중립화) 뒤 측정. ← 검수 기준으로 쓰는 값
  per_speaker_dev : 화자별로 따로 잰 편차(진단용).

의존: python3 (>=3.8), numpy, ffmpeg/ffprobe (PATH).
사용 예:
  python3 level_check.py final.mp3 --timeline turns.json --json report.json
  python3 level_check.py final.mp3 --skip-head 4 --skip-tail 6      # 인트로/아웃트로 음악 제외
  python3 level_check.py --predict script_turns.json                 # 합성 전 예측(B안)
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

SR = 48000  # 디코드 샘플레이트. RMS 측정에 리샘플 영향은 0.05 dB 미만.
FRAME_S = 0.1  # 게이팅 프레임(100 ms)

# ---------------------------------------------------------------------------
# 입력
# ---------------------------------------------------------------------------

def decode(path: str) -> Tuple[np.ndarray, int]:
    """ffmpeg으로 float32 PCM 디코드. 반환: (samples[n, ch], channels)."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=channels", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    ch = int(probe.stdout.strip().split(",")[0] or 1)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ar", str(SR), "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4")
    x = x[: len(x) // ch * ch].reshape(-1, ch)
    return x, ch


def load_timeline(path: str) -> List[dict]:
    """턴 타임라인 JSON: [{"speaker": "seoyeon", "start": 0.0, "end": 12.3}, ...]
    키 이름은 speaker/spk/name, start/begin, end/stop 중 아무거나 허용한다."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for k in ("turns", "timeline", "segments"):
            if k in data:
                data = data[k]
                break
    out = []
    for t in data:
        spk = t.get("speaker", t.get("spk", t.get("name")))
        s = float(t.get("start", t.get("begin")))
        e = float(t.get("end", t.get("stop")))
        out.append({"speaker": str(spk), "start": s, "end": e})
    return out

# ---------------------------------------------------------------------------
# 프레임 단위 분석
# ---------------------------------------------------------------------------

def db(p: float) -> float:
    return 10.0 * math.log10(p) if p > 0 else -120.0


def frame_power(x: np.ndarray) -> np.ndarray:
    """채널 평균 전력을 100 ms 프레임 단위로. 반환 길이 = floor(n / frame)."""
    f = int(FRAME_S * SR)
    n = len(x) // f
    mono_p = np.mean(x[: n * f] ** 2, axis=1)  # 채널 평균 전력(샘플별)
    return mono_p.reshape(n, f).mean(axis=1)


def frame_speaker(timeline: Optional[List[dict]], n_frames: int) -> np.ndarray:
    """프레임별 화자 인덱스(-1 = 턴 밖). timeline None이면 전부 0(단일 화자 취급)."""
    if not timeline:
        return np.zeros(n_frames, dtype=int)
    names = sorted({t["speaker"] for t in timeline})
    idx = {n: i for i, n in enumerate(names)}
    spk = np.full(n_frames, -1, dtype=int)
    for t in timeline:
        a = int(round(t["start"] / FRAME_S)); b = int(round(t["end"] / FRAME_S))
        spk[max(a, 0): min(b, n_frames)] = idx[t["speaker"]]
    return spk


def speech_gate(p: np.ndarray, abs_gate_db: float, rel_gate_db: float) -> np.ndarray:
    """EBU R128식 2단 게이트. 절대 게이트 통과 프레임의 평균 대비 rel_gate_db 아래는 제외."""
    keep = p > 10 ** (abs_gate_db / 10)
    if keep.sum() == 0:
        return keep
    ref = p[keep].mean()
    keep &= p > ref * 10 ** (rel_gate_db / 10)
    return keep

# ---------------------------------------------------------------------------
# 창 분할과 측정
# ---------------------------------------------------------------------------

@dataclass
class Window:
    index: int
    start: float
    end: float
    legacy_db: float            # volumedetect mean_volume 동등값
    gated_db: Optional[float]   # 발화 프레임만
    neutral_db: Optional[float] # 발화 프레임 + 화자 중립화
    speech_ratio: float         # 창 내 발화 프레임 비율
    speaker_frac: Dict[str, float] = field(default_factory=dict)  # 발화 프레임 중 화자 비율
    merged: bool = False        # 짧은 마지막 창이 직전 창에 합쳐짐


def window_bounds(n_frames: int, window_s: float, min_last_s: float,
                  skip_head_s: float, skip_tail_s: float) -> List[Tuple[int, int, bool]]:
    """프레임 인덱스 기준 (a, b, merged) 목록. 마지막 창이 min_last_s 미만이면 직전 창에 합친다."""
    w = int(round(window_s / FRAME_S))
    lo = int(round(skip_head_s / FRAME_S))
    hi = n_frames - int(round(skip_tail_s / FRAME_S))
    bounds = []
    a = lo
    while a < hi:
        b = min(a + w, hi)
        bounds.append([a, b, False])
        a = b
    if len(bounds) >= 2 and (bounds[-1][1] - bounds[-1][0]) * FRAME_S < min_last_s:
        last = bounds.pop()
        bounds[-1][1] = last[1]
        bounds[-1][2] = True
    return [tuple(b) for b in bounds]


def speaker_offsets(p: np.ndarray, keep: np.ndarray, spk: np.ndarray,
                    names: Sequence[str], manual: Optional[Dict[str, float]]) -> Dict[str, float]:
    """화자별 중립화 오프셋(dB). manual이 있으면 그대로, 없으면 각 화자의 에피소드 게이트 평균을
    전체 평균에 맞추는 오프셋을 자동 계산한다. 반환값을 '빼면' 중립화된다."""
    if manual:
        return {n: float(manual.get(n, 0.0)) for n in names}
    levels = {}
    for i, n in enumerate(names):
        m = keep & (spk == i)
        levels[n] = db(p[m].mean()) if m.any() else None
    valid = [v for v in levels.values() if v is not None]
    if not valid:
        return {n: 0.0 for n in names}
    ref = float(np.mean(valid))
    return {n: (levels[n] - ref if levels[n] is not None else 0.0) for n in names}


def measure(x: np.ndarray, timeline: Optional[List[dict]] = None, *, window_s: float = 15.0,
            min_last_s: float = 7.5, skip_head_s: float = 0.0, skip_tail_s: float = 0.0,
            abs_gate_db: float = -45.0, rel_gate_db: float = -15.0,
            manual_offsets: Optional[Dict[str, float]] = None) -> dict:
    p = frame_power(x)
    n = len(p)
    spk = frame_speaker(timeline, n)
    names = sorted({t["speaker"] for t in timeline}) if timeline else ["(single)"]
    keep = speech_gate(p, abs_gate_db, rel_gate_db)
    if timeline:
        keep &= spk >= 0  # 턴 밖(인트로/아웃트로/무음)은 게이트 지표에서 제외
    offs = speaker_offsets(p, keep, spk, names, manual_offsets)
    off_lin = np.ones(n)
    for i, nme in enumerate(names):
        off_lin[spk == i] = 10 ** (-offs[nme] / 10)
    p_neutral = p * off_lin

    wins: List[Window] = []
    # legacy는 '전체 샘플' 평균이어야 하므로 프레임 전력 평균으로 계산(프레임 경계 손실 <0.1 s).
    for idx, (a, b, merged) in enumerate(window_bounds(n, window_s, min_last_s, skip_head_s, skip_tail_s)):
        seg = slice(a, b)
        k = keep[seg]
        fr = {}
        if k.any() and timeline:
            s = spk[seg][k]
            fr = {nme: float((s == i).mean()) for i, nme in enumerate(names)}
        wins.append(Window(
            index=idx, start=a * FRAME_S, end=b * FRAME_S,
            legacy_db=db(p[seg].mean()),
            gated_db=db(p[seg][k].mean()) if k.any() else None,
            neutral_db=db(p_neutral[seg][k].mean()) if k.any() else None,
            speech_ratio=float(k.mean()),
            speaker_frac=fr, merged=merged))

    def dev(vals):
        v = [x for x in vals if x is not None]
        return (max(v) - min(v)) if len(v) >= 2 else None

    per_spk = {}
    for i, nme in enumerate(names):
        vals = []
        for a, b, _ in window_bounds(n, window_s, min_last_s, skip_head_s, skip_tail_s):
            m = keep[a:b] & (spk[a:b] == i)
            if m.sum() * FRAME_S >= 2.0:  # 2초 이상 발화한 창만
                vals.append(db(p[a:b][m].mean()))
        per_spk[nme] = dev(vals)

    return {
        "duration_s": n * FRAME_S,
        "windows": [asdict(w) for w in wins],
        "speaker_offsets_db": offs,
        "legacy_dev": dev([w.legacy_db for w in wins]),
        "gated_dev": dev([w.gated_db for w in wins]),
        "neutral_dev": dev([w.neutral_db for w in wins]),
        "per_speaker_dev": per_spk,
        "params": {"window_s": window_s, "min_last_s": min_last_s, "skip_head_s": skip_head_s,
                   "skip_tail_s": skip_tail_s, "abs_gate_db": abs_gate_db, "rel_gate_db": rel_gate_db,
                   "manual_offsets": manual_offsets},
    }

# ---------------------------------------------------------------------------
# B안: 합성 전 예측 (턴 타임라인 또는 대본으로부터)
# ---------------------------------------------------------------------------

def predict(timeline: List[dict], speaker_level_db: Dict[str, float], *, window_s: float = 15.0,
            min_last_s: float = 7.5, gap_s: float = 0.35) -> dict:
    """턴 타임라인과 화자별 목표 음량(dB)으로 창별 기대 음량을 계산한다.
    창 음량 = 전력 가중 혼합. 무음(턴 사이 gap)은 legacy 예측에만 반영한다.
    반환: predicted_legacy_dev, predicted_neutral_dev(≈화자 내부 분산이 0이면 0), 창별 화자 비율."""
    end = max(t["end"] for t in timeline)
    n = int(math.ceil(end / FRAME_S))
    spk = frame_speaker(timeline, n)
    names = sorted({t["speaker"] for t in timeline})
    lin = np.zeros(n)
    for i, nme in enumerate(names):
        lin[spk == i] = 10 ** (speaker_level_db[nme] / 10)
    ref = float(np.mean([speaker_level_db[nm] for nm in names]))
    lin_neutral = np.where(spk >= 0, 10 ** (ref / 10), 0.0)
    rows = []
    for idx, (a, b, merged) in enumerate(window_bounds(n, window_s, min_last_s, 0, 0)):
        s = spk[a:b]
        act = s >= 0
        fr = {nme: float((s[act] == i).mean()) if act.any() else 0.0 for i, nme in enumerate(names)}
        rows.append({
            "index": idx, "start": a * FRAME_S, "end": b * FRAME_S, "merged": merged,
            "legacy_db": db(lin[a:b].mean()),
            "gated_db": db(lin[a:b][act].mean()) if act.any() else None,
            "neutral_db": db(lin_neutral[a:b][act].mean()) if act.any() else None,
            "speech_ratio": float(act.mean()), "speaker_frac": fr,
        })
    def dev(key):
        v = [r[key] for r in rows if r[key] is not None]
        return (max(v) - min(v)) if len(v) >= 2 else None
    return {"windows": rows, "predicted_legacy_dev": dev("legacy_db"),
            "predicted_gated_dev": dev("gated_db"), "predicted_neutral_dev": dev("neutral_db"),
            "max_single_speaker_window_frac": max(max(r["speaker_frac"].values()) for r in rows if r["speaker_frac"])}


def timeline_from_script(turns: List[dict], chars_per_sec: float = 5.5, gap_s: float = 0.35) -> List[dict]:
    """대본 턴 [{"speaker":..., "text":...}]에서 길이를 추정해 타임라인을 만든다.
    chars_per_sec는 한국어 TTS 발화 속도 추정치(기본 5.5자/초). 실제 TTS 길이가 있으면 그걸 써라.
    공백은 세지 않는다."""
    out, t = [], 0.0
    for tr in turns:
        text = tr.get("text", "")
        d = max(len(text.replace(" ", "")) / chars_per_sec, 0.8)
        out.append({"speaker": str(tr["speaker"]), "start": t, "end": t + d})
        t += d + gap_s
    return out

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def fmt(v):
    return "   -  " if v is None else f"{v:6.2f}"


def print_report(rep: dict, threshold: float) -> None:
    print(f"길이 {rep['duration_s']:.1f}s  화자 중립화 오프셋(dB): "
          + ", ".join(f"{k}={v:+.2f}" for k, v in rep["speaker_offsets_db"].items()))
    print(f"{'창':>3} {'시작':>7} {'끝':>7} {'legacy':>7} {'gated':>7} {'neutral':>7} {'발화%':>5}  화자비율")
    for w in rep["windows"]:
        fr = " ".join(f"{k[:6]}={v:.2f}" for k, v in w["speaker_frac"].items())
        tag = " (합침)" if w["merged"] else ""
        print(f"{w['index']:>3} {w['start']:>7.1f} {w['end']:>7.1f} {fmt(w['legacy_db'])} {fmt(w['gated_db'])} "
              f"{fmt(w['neutral_db'])} {w['speech_ratio']*100:>5.0f}  {fr}{tag}")
    print()
    print(f"legacy_dev  = {fmt(rep['legacy_dev'])} dB   (기존 volumedetect 방식, 참고용)")
    print(f"gated_dev   = {fmt(rep['gated_dev'])} dB   (발화 구간만)")
    print(f"neutral_dev = {fmt(rep['neutral_dev'])} dB   (발화 구간 + 화자 간격 중립화) ← 판정값")
    print("per_speaker_dev: " + ", ".join(f"{k}={fmt(v)}" for k, v in rep["per_speaker_dev"].items()))
    verdict = rep["neutral_dev"] is not None and rep["neutral_dev"] <= threshold
    print(f"판정(neutral_dev ≤ {threshold}): {'PASS' if verdict else 'FAIL'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", nargs="?", help="측정할 오디오 파일")
    ap.add_argument("--timeline", help="턴 타임라인 JSON (화자/start/end)")
    ap.add_argument("--window", type=float, default=15.0)
    ap.add_argument("--min-last", type=float, default=7.5, help="이보다 짧은 마지막 창은 직전 창에 합침")
    ap.add_argument("--skip-head", type=float, default=0.0, help="앞부분 제외(초), 인트로 음악 등")
    ap.add_argument("--skip-tail", type=float, default=0.0, help="뒷부분 제외(초), 아웃트로 등")
    ap.add_argument("--abs-gate", type=float, default=-45.0, help="절대 게이트 dBFS")
    ap.add_argument("--rel-gate", type=float, default=-15.0, help="상대 게이트 dB")
    ap.add_argument("--offset", action="append", default=[],
                    help="화자 중립화 오프셋 수동 지정, 예: --offset seoyeon=+5 (생략 시 자동)")
    ap.add_argument("--threshold", type=float, default=3.5, help="판정 임계값 dB (기본 3.5, 바꾸지 말 것)")
    ap.add_argument("--json", help="보고서를 JSON으로 저장")
    ap.add_argument("--predict", metavar="TURNS_JSON",
                    help="합성 전 예측 모드. 타임라인 JSON 또는 대본 턴 JSON([{speaker,text}])")
    ap.add_argument("--level", action="append", default=[],
                    help="예측용 화자 목표 음량, 예: --level seoyeon=-18 --level jihoon=-23")
    ap.add_argument("--cps", type=float, default=5.5, help="대본 기반 예측 시 발화 속도(자/초)")
    a = ap.parse_args(argv)

    if a.predict:
        with open(a.predict, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("turns", data.get("timeline", data))
        if data and "text" in data[0] and "end" not in data[0]:
            tl = timeline_from_script(data, a.cps)
        else:
            tl = load_timeline(a.predict)
        names = sorted({t["speaker"] for t in tl})
        lv = {k: float(v) for k, v in (s.split("=") for s in a.level)}
        for nme in names:
            lv.setdefault(nme, -20.0)
        rep = predict(tl, lv, window_s=a.window, min_last_s=a.min_last)
        print(f"예측 legacy_dev  = {fmt(rep['predicted_legacy_dev'])} dB")
        print(f"예측 gated_dev   = {fmt(rep['predicted_gated_dev'])} dB")
        print(f"예측 neutral_dev = {fmt(rep['predicted_neutral_dev'])} dB (화자 내부 분산 0 가정)")
        print(f"단일 화자 최대 비율 창: {rep['max_single_speaker_window_frac']:.2f}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
        return 0

    if not a.audio:
        ap.error("audio 또는 --predict 중 하나는 필요합니다")
    x, _ = decode(a.audio)
    tl = load_timeline(a.timeline) if a.timeline else None
    manual = {k: float(v) for k, v in (s.split("=") for s in a.offset)} or None
    rep = measure(x, tl, window_s=a.window, min_last_s=a.min_last, skip_head_s=a.skip_head,
                  skip_tail_s=a.skip_tail, abs_gate_db=a.abs_gate, rel_gate_db=a.rel_gate,
                  manual_offsets=manual)
    print_report(rep, a.threshold)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    return 0 if (rep["neutral_dev"] is not None and rep["neutral_dev"] <= a.threshold) else 1


if __name__ == "__main__":
    sys.exit(main())
