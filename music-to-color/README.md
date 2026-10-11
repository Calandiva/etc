# 음악 → 색채 변환기 (Music to Color)

음악 파일의 주파수를 분해해서 **가시광선 색**으로 바꿔 보여주는 브라우저 시각화 도구입니다.
소리는 한 가지 음만 나지 않으므로(화음·여러 악기) 여러 주파수대를 동시에 여러 색으로
보여주고, 그 색이 매 프레임 제멋대로 왔다갔다 하지 않도록 **격자 칸마다 주파수대를
미리 고정**해 둡니다. 소리가 커지면 그 칸이 밝아지고, 작아지면 어두워질 뿐 위치는
흔들리지 않습니다.

## 바로 쓰기

`index.html`을 브라우저로 열고(서버 불필요, 외부 전송 없음) 음악 파일을 올리면 재생과
동시에 격자가 색으로 채워집니다. 우측 상단 **⛶ 전체화면** 버튼으로 화면을 최대화할 수
있고, **⚙ 설정** 버튼으로 패널을 접었다 펼 수 있습니다. 창 크기·전체화면 전환 시
격자는 항상 가능한 가장 큰 정사각형으로 다시 배치됩니다.

파일이 없어도 **샘플 음원** 패널의 버튼 4개(주파수 스윕·아르페지오·리듬 비트·지속 화음)로
바로 재생해 볼 수 있습니다. 모두 저작권 걱정 없는 합성 사인파로, `python -m
music_to_color.make_samples`(아래 Python 섹션)로 `samples/`에 다시 만들 수 있습니다.

## 사용자가 직접 설정하는 것

| 설정 | 의미 | 기본값 |
|---|---|---|
| 소리 주파수 구간 | 가청 20~20,000Hz 중 실제로 색으로 바꿀 구간 | 100~4,000Hz |
| 가시광선 주파수 구간 | 매핑될 빛의 주파수(THz) 구간 | 405~790THz (가시광선 전체) |
| 연결 방식 | 두 구간을 잇는 곡선 모양: 리니어 / 로그 / 지수 | 로그 |
| 격자 크기 | 한 변의 칸 수(10~1000). 칸 수 = 격자 크기² | 32 (1,024칸) |
| 부드러움 | 프레임 간 밝기 평활(떨림 완화) | 0.7 |
| 최소 밝기 | 무음일 때도 격자 패턴이 희미하게 보이는 정도 | 0.08 |
| 칸 선명하게 | 칸 경계를 또렷하게/부드럽게 | 선명하게 |

**연결 방식**이 로그일수록 사람의 음정 체감(옥타브=배수)에 가깝게 저음 구간이
넓게, 고음 구간이 좁게 색이 퍼집니다. 지수는 반대로 저음을 압축하고 고음을 넓힙니다.

## 동작 원리

1. **격자 = 고정 주파수대.** 격자가 N×N이면 소리 주파수 구간을 N² 개 밴드로 쪼갭니다.
   밴드 경계는 "연결 방식"에 따라 균등(리니어)하거나 로그/지수로 휘어진 간격으로
   나뉘고, 각 밴드는 같은 비율 위치(t)의 가시광선 주파수로 매핑되어 **고정 RGB**를
   갖습니다. 이 고정 색 배치는 설정이 바뀔 때만 다시 계산됩니다.
2. **실시간 분석.** Web Audio API `AnalyserNode`로 FFT를 돌리고, 매 프레임 각 밴드의
   고정 주파수 지점에서 가장 가까운 두 FFT bin을 선형보간해 밝기(0~1)를 구합니다.
   격자 칸 수가 FFT 해상도보다 훨씬 많아도(최대 1,000,000칸) 빈 칸 없이 매끈합니다.
3. **렌더링.** N×N 픽셀짜리 오프스크린 캔버스에 색을 채운 뒤, 화면 캔버스로 확대해
   그립니다. 칸 수가 많아도 매 프레임 작은 버퍼만 갱신하므로 가볍습니다.
4. **파장 → RGB.** 가시광선 주파수(THz)를 파장(nm)으로 바꾼 뒤, 표준 근사식(Dan
   Bruton 알고리즘)으로 RGB를 계산합니다.

## 파일 구성

```
index.html, css/style.css          — 단일 화면 UI (패널 설정, 캔버스, 전체화면)
js/colorModel.js                   — 주파수 ↔ 색채 매핑 수학 (스케일, 밴드, 파장→RGB)
js/audioEngine.js                  — Web Audio 재생 + FFT → 밴드별 밝기
js/visualizer.js                   — N×N 격자 캔버스 렌더러(리사이즈/전체화면 대응)
js/app.js                          — UI 연결, 메인 루프

python/music_to_color/color_model.py  — js/colorModel.js 와 동일한 매핑 수학(Python)
python/music_to_color/audio_render.py — WAV 오프라인 분석 CLI (JSON/영상 출력)
python/music_to_color/make_samples.py — index.html의 샘플 재생용 합성 WAV 4개 생성
python/tests/test_color_model.py      — 매핑 수학 단위 테스트
samples/                              — 합성 샘플 음원 (sweep/arpeggio/rhythm_beat/sustained_chord)
```

JS와 Python은 같은 수학(스케일 정규화, 파장→RGB, 밴드 생성)을 각자 구현하며,
위 예시로 두 쪽 결과가 일치함을 확인했습니다(밴드[0]: 100.72Hz → 405.75THz →
RGB(183,0,0), 양쪽 동일).

### Python CLI (선택 — 오프라인 분석/미리보기 영상)

브라우저 없이 WAV 파일을 분석해 프레임별 밴드 밝기를 JSON으로 뽑거나, `imageio`가
설치돼 있으면 미리보기 영상도 만들 수 있습니다.

```bash
cd python
pip install -r requirements.txt   # numpy
pip install -e .                  # music_to_color 패키지 설치(테스트/CLI용)

python -m music_to_color.audio_render \
  --input song.wav --output-json frames.json \
  --grid-size 32 --sound-min 100 --sound-max 4000 --scale log

# 영상까지 원하면: pip install imageio imageio-ffmpeg 후
python -m music_to_color.audio_render --input song.wav --output-video preview.mp4 ...

pytest tests/   # 매핑 수학 단위 테스트
```

## 주의

격자를 500×500(25만 칸) 이상으로 올리면 기기에 따라 느려질 수 있습니다(화면에
경고가 뜹니다). 대부분의 음악은 32×32~128×128 정도에서 이미 충분히 풍부하게
보입니다.
