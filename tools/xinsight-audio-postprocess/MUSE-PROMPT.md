# Xinsight 후처리 검수 개선 작업 (process.py, SKILL.md)

## 배경
최근 나흘간 "15초 구간 평균 음량 편차 ≤ 3.5 dB" 검사에서 반복 실패했다. 원인은 측정 방식이다.
기존 측정(15초 고정 창 × ffmpeg volumedetect mean_volume의 최대−최소)은 무음·쉼·꼬리 무음을 평균에 포함하고,
화자 간 의도된 간격(+5 dB)을 그대로 편차로 센다. 간격 5 dB에서는 한 화자가 창을 독점하면 편차가 4~5 dB가
되므로 대본을 고쳐 재합성해도 통과할 수 없다(수학적 충돌). 대본 재합성 3회 루프는 효과가 없었다.

개선된 측정기 `level_check.py`가 준비돼 있다. 발화 구간만 평균하고 화자 간격을 중립화한 뒤 편차를 잰다.
**임계값 3.5 dB는 그대로다. 측정 방법만 바뀐다.**

## 가져오기
```bash
cd ~/workspace
git clone -b claude/peaceful-brown-ror6ap https://github.com/piman-code/news.git xinsight-levelcheck
cp xinsight-levelcheck/tools/xinsight-audio-postprocess/level_check.py ~/workspace/custom-skills/xinsight-audio-postprocess/
cd xinsight-levelcheck/tools/xinsight-audio-postprocess && python3 test_level_check.py   # ALL OK 확인
```
README.md(통합 지점), SKILL-addendum.md(검수 절차)를 먼저 읽어라.

## 할 일 (순서대로)

### 1. 측정 복제 확인
11/10 모닝 시도본 4개의 최종 오디오에 대해 `python3 level_check.py <오디오> --timeline <턴 타임라인 JSON>`을 돌리고,
출력의 `legacy_dev`가 11/10 실행 기록의 편차 수치(4.4 / 4.4 / 5.6 / 4.4)와 ±0.2 dB 안에서 맞는지 확인한다.
턴 타임라인(화자, 시작, 끝)이 저장돼 있지 않으면 턴별 TTS 파일 길이로 만들어라(합성·평탄화 게인은 길이를 바꾸지 않는다).
맞지 않으면 process.py의 기존 측정 코드(창 분할 방식, 입력 파일, 채널)를 보고 차이 원인을 적어라. 맞을 때까지 다음으로 가지 마라.

### 2. process.py 수정
- 검수 단계의 15초 창 volumedetect 루프를 `level_check.measure(x, timeline, ...)` 호출로 바꾼다.
  판정은 `neutral_dev <= 3.5`. `legacy_dev`, `gated_dev`, `per_speaker_dev`, 화자 오프셋, 창별 표는 로그에 그대로 남긴다.
- 턴별 TTS를 이어 붙이는 단계에서 턴 타임라인 JSON을 저장해 검수에 넘긴다.
- 인트로/아웃트로 음악이 있으면 그 길이를 `skip_head_s/skip_tail_s`로 넘기거나 타임라인 밖에 두어 제외한다.
- 재제작 분기: 편차 실패로는 대본 재작성·재합성을 돌리지 않는다. SKILL-addendum.md의 "실패했을 때 할 일" 순서를 코드와 로그 메시지에 반영한다.
  대본 재합성은 전사 검수 붕괴 등 내용 문제에만 남긴다.
- 대본 확정 직후 `level_check.py --predict`를 돌려 `predicted_legacy_dev`와 단일 화자 최대 비율 창을 로그에 기록한다(판정에는 쓰지 않음).

### 3. SKILL.md 수정
SKILL-addendum.md 내용을 SKILL.md의 검수 절차에 넣는다. 기준 수치는 한 글자도 바꾸지 않는다.

### 4. 검증 (실제 오디오)
아래 사례 전부에 대해 네 지표를 표로 만든다. 열: 사례 / legacy_dev / gated_dev / neutral_dev / per_speaker_dev(서연, 지훈) / 새 판정.
- 11/10 모닝 시도 1~4
- 11/7 이브닝 시도 1~4 (6.2 → 5.0 → 4.7 → 4.5)
- 통과 사례: 11/8 모닝 2회차(2.1), 11/9 모닝 1회차(2.7)
새 판정값(neutral_dev)으로도 3.5를 넘는 사례가 있으면 숨기지 말고 창별 표와 함께 그대로 보고한다.
통과 사례가 새 방식에서 실패로 바뀌면 그것도 보고한다.

### 5. 보고
- 1단계 복제 결과(맞았는지, 차이 원인)
- 4단계 비교표
- process.py에서 바꾼 함수 이름과 줄 범위, SKILL.md에서 바꾼 절
- 확인하지 못한 것, 추정으로 남은 것

## 건드리지 않는 것
- 임계값 3.5 dB 및 라우드니스(−15.5~−12.8 LUFS), 트루피크(<0 dB), 화자 간격(3.5~6.5 dB), 전사 검수 기준
- 서연·지훈 음성 ID, podcast-helper, RSS 스크립트, 발행 파이프라인
- 기준을 바꿔야 한다고 판단되면 근거와 함께 "제안"으로만 적고 코드로 바꾸지 마라

## 원칙
- 측정으로 확인한 것과 추정을 구분해서 써라.
- 새 방식으로 통과시키기 위해 게이트 값(abs −45 dBFS, rel −15 dB)이나 min_last(7.5 s)를 사례별로 조정하지 마라. 바꿔야 한다면 전 사례에 같은 값을 쓰고 이유를 적어라.
- 작업 전 process.py, SKILL.md 원본을 백업해 두고, 변경 후 기존 통과 사례가 계속 통과하는지 확인한 뒤 마무리한다.
