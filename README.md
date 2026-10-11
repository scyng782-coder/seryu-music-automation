# 세류 음악 자동화

로컬 Suno 다운로드 음원을 마스터링하고 리릭비디오를 만드는 Python + FFmpeg CLI입니다. 음악과 첫 영상은 0초부터 시작하고 별도 제목 화면은 없습니다.

## 설치

Python 3.10 이상, FFmpeg/ffprobe (libx264, AAC, loudnorm, subtitles/libass, drawtext 지원), 한글 폰트가 필요합니다. Linux에서 검증했습니다.

```sh
# Ubuntu / Debian 시스템 의존성
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-cjk
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --check
```

Windows/macOS는 해당 OS용 FFmpeg와 한글 폰트를 설치하고 FFmpeg/ffprobe를 PATH에 추가하세요. 가상환경 Python으로 동일 명령을 실행할 수 있지만 해당 플랫폼은 아직 검증하지 않았습니다.

## 샘플 실행

```sh
.venv/bin/python examples/generate_sample.py
.venv/bin/python main.py --config config.yaml
.venv/bin/python -m unittest discover -s tests -v
```

샘플은 6초 사인파, 이미지 8장, 1초 동영상, 한글 SRT, 샘플 세류 로고입니다. 실제 음악이나 정식 로고가 아닙니다. 샘플 디렉터리가 비어 있지 않으면 덮어쓰지 않습니다.

결과: `output/mastered.wav`, `youtube.mp4`, `shorts.mp4`, `report.json`. 기존 결과를 덮어쓰지 않습니다. 재실행 시 설정의 `output_dir`를 새 경로로 바꾸세요.

## 실제 음원 사용

`config.yaml`을 복사해 아래 항목을 바꿉니다. 상대 경로는 설정 파일의 위치를 기준으로 합니다.

| 항목 | 설명 |
| --- | --- |
| audio | 다운로드한 WAV/MP3 등 FFmpeg가 읽을 수 있는 로컬 음원 |
| lyrics | 사용자가 타이밍을 지정한 UTF-8 SRT 자막 |
| logo | 투명 PNG 권장. 전체 영상 오른쪽 위에 표시 |
| media | 순서대로 사용할 이미지/동영상. type은 image 또는 video |
| formats | [youtube, shorts] 또는 한 형식만 선택 |
| output_dir | 기존 결과가 없는 출력 경로 |
| mastering | 기본 -14 LUFS, 최대 -2 dBTP, LRA 11 |
| next_song | 마지막 지정 초 동안 표시할 다음 곡 문구 |
| font | 설치된 폰트 이름, 기본 Noto Sans CJK KR |
| fps, threads, preset | 기본 30fps, CPU 스레드 2개, x264 fast |

미디어마다 거의 같은 프레임 수를 배분합니다. 이미지 장수 제한은 없으며 샘플은 8장입니다. 짧은 동영상은 반복하고 긴 동영상은 할당 시간만큼 자릅니다. 동영상 원래 소리는 제외합니다. 화면 비율은 중앙 크롭으로 맞추므로 주요 피사체 위치를 확인하세요. 다음 곡 안내는 음악 끝에 겹쳐 표시하고 추가 무음 화면을 붙이지 않습니다.

출력은 1920×1080 / 1080×1920, H.264 yuv420p + AAC 192kbps, MP4 faststart입니다. 마스터는 48kHz 스테레오 24bit WAV입니다. EBU R128 기반 2회 loudnorm으로 정규화하고 필요하면 동적 제한을 적용합니다. 원본 파일은 읽기만 하며 SHA-256으로 보존을 검증합니다. 마스터 true peak와 최종 AAC true peak를 측정하고 최종 출력이 0 dBTP 이상이면 실패합니다. 분석은 report.json에 기록됩니다. 이미 클리핑된 원본 복구나 창작적 EQ/압축은 포함하지 않습니다.

자막은 영상에 직접 입힙니다. 자동 타이밍 추정은 포함하지 않습니다. 한글이 네모로 보이면 한글 폰트를 설치하고 font를 바꾸세요. 임시 파일은 자동 삭제됩니다. 전체 음원 길이에 비례한 디스크와 CPU 시간이 필요하며 중간 영상과 최종 영상의 두 번 인코딩을 수행합니다.

## 외부 서비스 및 API 키

현재 기능은 외부 서비스나 API 키 없이 실행됩니다. Suno 음원, 이미지·동영상, 가사 SRT, 로고는 로컬 파일로 제공합니다. Suno 다운로드 자동화, AI 이미지 생성, 가사 자동 정렬, YouTube 업로드는 포함하지 않습니다. 향후 추가하려면 서비스별 공식 API 접근 권한, 생성 서비스 키 또는 YouTube OAuth 인증이 필요합니다. 키를 설정 파일이나 Git에 저장하지 마세요.

## 검증

통합 테스트는 생성한 8장 이미지와 동영상을 실제 두 해상도로 렌더링합니다. H.264/AAC, 길이, 시작 시각, 마스터 LUFS/true peak, AAC true peak, 원본 해시, 결과 덮어쓰기 거부와 잘못된 설정을 검사합니다. 네트워크나 API 키를 사용하지 않습니다.
