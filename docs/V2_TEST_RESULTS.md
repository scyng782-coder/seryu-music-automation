# 세류 음악 자동화 2.0 검증 결과

기준 main: b002ea3. 개발 브랜치: feature/seryu-automation-v2.
환경: Linux, Python 3.12.14, FFmpeg/ffprobe 7.1.5, Gradio 6.30.0, Noto Sans CJK KR.

## 자동 테스트

명령: `/workspace/seryu-venv/bin/python -m unittest discover -s tests -v`

**11개 통과, 36.375초. 실패·스킵 없음.** 기존 test_pipeline.py의 3개 테스트는 수정하지 않았습니다.

- 기존 마스터링, 높은 피크 제한, 무음 거부, 두 해상도 출력, 원본 해시 및 덮어쓰기 거부
- 신규 크로스페이드·별도 크롭·자막 설정 E2E, 음원/영상 길이 동기화, 0초 시작·첫 프레임 비검정, pcm_s24le 출력
- 반복 렌더링의 고유 revision 및 기존 결과 해시 보존
- FFmpeg 실행 중 취소, 작업 잠금과 세션 소유권, 취소 후 기존 revision 보존
- 한국어 가사 순서·반복 후렴·연주 공백·누락 타이밍·유효 SRT 검사 (합성 단어 시간 사용)
- AI 의존성/모델 실패 시 비정상 종료하고 가짜 타이밍·출력 파일을 생성하지 않는 계약
- 인증된 실제 Gradio HTTP 업로드 → 자막 구간 청취 → 수동 SRT 제작 → 결과 파일 요청
- 비로그인 401, 비공개 입력 파일 접근 403, 1MB 테스트 업로드 제한 413
- 경로 탈출·심볼릭 링크·위장 MP4 플레이리스트·중복 이미지 순서·파일 용량 거부
- 로그인 정보 필수 및 Unicode 로그인 비교

`main.py --check`, `pip check`, `git diff --check` 통과. Gradio 클라이언트/큐 내부의 ResourceWarning/Pandas 폐기 예정 API 경고가 발생했으나 테스트는 통과했습니다.

## 별도 보존한 샘플 E2E

명령: `python examples/run_v2_sample.py --root /workspace/seryu-v2-validation`

사인파 6초, 이미지 8장, MP4 1개, 한글 SRT, 샘플 PNG 로고로 실제 두 형식을 렌더링했습니다. 결과는 저장소 밖의 고유 폴더에 보존됩니다.

- youtube.mp4: 1920×1080 H.264/AAC, 오디오·영상 각각 6.000초
- shorts.mp4: 1080×1920 H.264/AAC, 오디오·영상 각각 6.000초
- mastered.wav: 48kHz stereo pcm_s24le, -14.05 LUFS / -13.29 dBTP
- 최종 AAC (두 형식): -14.06 LUFS / -13.04 dBTP (클리핑 없음)
- lyrics.srt, report.json 포함. 전환 0.1초, 가로/세로 크롭 설정 적용

기존 config.yaml, requirements.txt, examples/generate_sample.py, TEST_RESULTS.md 및 기존 테스트는 main 대비 변경하지 않았습니다. 기존 examples/generated와 output은 새 작업에 사용하거나 덮어쓰지 않았으며 기존 음원 해시는 기존 보고서와 일치합니다. 사용자 저작물·모델·API 키를 Git에 포함하지 않습니다.

## 미검증 / 제약

- GPU: nvidia-smi와 /dev/nvidia 장치 없음. GPU 추론 미검증.
- huggingface.co: HTTPS 프록시 CONNECT 403. 모델 다운로드 불가. 우회 또는 TLS/체크섬 검증 비활성화 없음.
- AI 패키지 전체 설치 및 모델 추론은 실행하지 않았습니다. WhisperX 배포 wheel의 한국어 모델 매핑·함수 인수·의존성 요구는 확인했습니다.
- Gradio와 WhisperX의 huggingface-hub 요구가 충돌하여 AI는 SERYU_AI_PYTHON으로 별도 가상환경에서 실행하도록 분리했습니다. AI 환경 전체 설치는 미검증입니다.
- **실제 한국어 Suno 노래 가사 타이밍 정확도·음악 적합성은 검증되지 않았습니다.** 합성 매칭 테스트를 모델 검증으로 간주하지 않습니다. 자동 정렬은 실험 기능이며 모든 결과를 청취·수정 후 승인하도록 합니다. 수동 SRT 경로는 검증 완료입니다.
- 실제 Suno 음원 청감 평가, Windows/macOS, Android 실기기 및 인터넷 공개 배포는 수행하지 않았습니다.

따라서 PR은 Draft로 제출하고, 실제 한국어 노래 정렬 검증은 docs/ALIGNMENT_VALIDATION.md의 절차로 후속 확인해야 합니다. 외부 서비스 설정이나 main 병합은 수행하지 않습니다.
