# xinsight-audio-postprocess / level_check

Xinsight 팟캐스트 후처리 검수 중 **"15초 구간 평균 음량 편차 ≤ 3.5 dB"** 항목의 측정기를
의도(음량 일관성)에 맞게 재정의한 모듈이다. 임계값 3.5 dB는 바꾸지 않는다.

## 왜 바꾸는가 (요약)

기존 측정은 `15초 고정 창 × ffmpeg volumedetect mean_volume`의 최대−최소였다.
mean_volume은 창 안 **모든 샘플**의 RMS라서 다음이 그대로 편차에 섞인다.

| 요인 | 편차 기여(합성 데이터 측정) | 비고 |
|---|---|---|
| 화자 간 의도된 간격 5 dB + 한 화자가 창을 독점 | 4.9~5.4 dB | 턴을 평균 3초로 잘라도 3.7 dB |
| 마지막 부분 창에 꼬리 무음 2/5/10초 | 2.2 / 3.7 / 5.5 dB | 창 하나가 검사를 떨어뜨림 |
| 한 화자의 쉼이 2~3배 길 때 | 1.5~1.9 dB | |
| 슬라이딩 창으로 바꾸기 | 오히려 +0.3~0.9 dB | 정렬 방식은 해법 아님 |

간격 G와 "큰 화자 비율" p인 창의 음량은 `10·log10(1 + p·(10^(G/10) − 1))`이다.
G = 5일 때 편차 ≤ 3.5가 되려면 모든 창의 p가 폭 0.57 띠에 들어야 한다.
즉 **화자 간격 목표(+5 dB)와 기존 편차 측정은 수학적으로 충돌**하며, 대본 수정으로는 풀리지 않는다.
(재현: 저장소 밖 세션 스크래치의 sim2.py, 결과 요약은 이 표)

## 새 지표

`level_check.py`는 한 번에 네 값을 낸다.

| 지표 | 정의 | 용도 |
|---|---|---|
| `legacy_dev` | 기존 방식 그대로(volumedetect와 ±0.03 dB 일치) | 로그 연속성, 비교용 |
| `gated_dev` | 100 ms 프레임 중 발화 프레임만 평균(절대 −45 dBFS, 상대 −15 dB 2단 게이트) | 쉼·무음·꼬리 영향 제거 |
| `neutral_dev` | gated + 화자별 에피소드 평균을 공통 기준에 맞춘 뒤 측정 | **판정값** |
| `per_speaker_dev` | 화자별로 따로 잰 창 편차 | 진단: 평탄화가 화자 내부에서 잘 됐는지 |

추가 처리: 마지막 창이 7.5초 미만이면 직전 창에 합침(`--min-last`),
인트로/아웃트로 음악은 `--skip-head/--skip-tail` 또는 턴 타임라인 밖 구간으로 제외.
화자 중립화 오프셋은 자동 계산(각 화자의 게이트 평균 → 전체 평균)이며 `--offset 이름=dB`로 고정할 수 있다.

## 사용

```bash
# 검수(판정): 종료코드 0=PASS, 1=FAIL
python3 level_check.py final.mp3 --timeline turns.json --json report.json

# 타임라인이 없으면 gated 만 의미 있음 (neutral = gated). 반드시 타임라인을 넘길 것.
python3 level_check.py final.mp3 --skip-head 4 --skip-tail 6

# 합성 전 예측(B안): 대본 턴 [{speaker,text}] 또는 타임라인 JSON
python3 level_check.py --predict script_turns.json --level 서연=-18 --level 지훈=-23 --cps 5.5
```

타임라인 JSON 형식: `[{"speaker": "서연", "start": 0.0, "end": 12.3}, ...]`
(`turns`/`timeline`/`segments` 키로 감싸도 되고, `spk`/`begin`/`stop` 별칭도 받는다.)

의존: python3 ≥ 3.8, numpy, ffmpeg/ffprobe.

## process.py 통합 지점 (파일 확인 후 확정)

1. **검수 단계**: 15초 창 volumedetect 루프를 `level_check.measure(x, timeline)` 호출로 교체.
   판정은 `neutral_dev ≤ 3.5`. `legacy_dev`, `gated_dev`, 창별 표는 로그에 그대로 남긴다.
2. **턴 타임라인 전달**: 턴별 TTS 파일을 이어 붙이는 단계에서 (화자, 시작, 끝)을 JSON으로 저장해 검수에 넘긴다.
   평탄화·간격 보정 게인은 타임라인에 영향을 주지 않으므로 어느 단계에서 기록해도 된다.
3. **재제작 분기**: 편차 실패 시 대본 재작성·재합성을 바로 돌리지 않는다.
   `per_speaker_dev`가 3.5를 넘는 화자가 있으면 그 화자의 턴별 게인(평탄화)을 점검하고,
   `neutral_dev`만 실패하고 `per_speaker_dev`는 정상이면 중립화 오프셋/타임라인 정합을 먼저 본다.
   대본 재합성은 전사 검수 붕괴 등 내용 문제에만 쓴다.
4. **합성 전 예측**: 대본 확정 직후 `--predict`로 `predicted_legacy_dev`와 단일 화자 창 비율을 기록한다.
   판정에는 쓰지 않고(새 판정값은 화자 분포에 무관), 추세 관찰용으로 남긴다.

## 검증 계획 (실제 오디오 수령 후)

11/10 모닝 시도본 4개, 11/7 이브닝 4개, 통과 사례 2개에 대해
`legacy_dev`가 실행 기록 수치와 일치하는지 먼저 확인한 뒤, 네 지표를 표로 비교한다.
`neutral_dev`가 3.5를 넘는 사례가 남으면 그대로 보고한다.

## 테스트

```bash
python3 test_level_check.py   # 합성 데이터, ffmpeg 필요, 약 1분
```
