# 실행 검증 결과

- 환경: Python 3.12.14 / FFmpeg 7.1.5 / Linux / Noto Sans CJK KR
- 명령: `/workspace/seryu-venv/bin/python -m unittest discover -s tests -v`
- 결과: 3개 테스트 통과 (14.087초)
- 실제 1920×1080, 1080×1920 영상 출력: H.264 / AAC, 각각 6.000초
- 이미지 8장 및 반복 동영상 혼합, 프레임 배경 전환 검사 통과
- 원본 음원 SHA-256 보존, 기존 결과 덮어쓰기 거부, 잘못된 fps 거부 확인
- 높은 피크 입력의 -2 dBTP 제한, 무음 입력 거부 확인
- 독립 CLI 샘플 실행도 완료: `python examples/generate_sample.py`, `python main.py --config config.yaml`
- CLI 샘플 마스터: -14.05 LUFS / -13.29 dBTP
- CLI 샘플 AAC (두 형식): -14.06 LUFS / -13.04 dBTP
- 첫 화면 한글 가사·로고, 마지막 다음 곡 안내는 추출 프레임으로 직접 확인
- `pip check`, `git diff --check` 통과

사인파 및 합성 미디어로 검증했습니다. 실제 Suno 음원에 대한 청감 평가, Windows/macOS 실행, 외부 서비스 연동은 수행하지 않았습니다. 샘플 피크가 목표보다 낮은 것은 저 crest-factor 사인파의 정상적인 결과이며, 높은 피크 제한은 별도 입력으로 검사했습니다.

생성한 샘플과 결과 영상은 `.gitignore` 대상입니다. 환경 내 `examples/generated/`, `output/`에 있으며 Git에는 포함하지 않습니다.
