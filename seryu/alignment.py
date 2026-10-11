"""Optional faster-whisper anchors + WhisperX CTC forced alignment.

Real singing suitability requires a licensed Korean vocal validation set. Model
scores are review heuristics, not calibrated probabilities of timing accuracy.
"""
import json
import os
from pathlib import Path
import sys

from seryu.lyrics import anchor_lines, lyric_lines, normalized


def align(audio, text, model='small', device='cpu', cache=None):
    try:
        from faster_whisper import WhisperModel
        import whisperx
    except ImportError as exc:
        raise RuntimeError('AI 의존성이 없습니다. requirements-ai.txt를 설치하거나 수동 SRT를 사용하세요.') from exc
    lines = lyric_lines(text)
    cache = str(cache or Path.home() / '.cache' / 'seryu-models')
    # Download only with explicit operator opt-in; otherwise cached models only.
    local_only = os.environ.get('SERYU_ALLOW_MODEL_DOWNLOAD') != '1'
    asr = WhisperModel(model, device=device, compute_type='int8' if device == 'cpu' else 'float16',
                       download_root=cache, local_files_only=local_only)
    segments, _ = asr.transcribe(str(audio), language='ko', word_timestamps=True,
                                vad_filter=False, condition_on_previous_text=False)
    words = [{'word': w.word, 'start': w.start, 'end': w.end, 'probability': w.probability}
             for segment in segments for w in (segment.words or [])]
    rows = anchor_lines(lines, words)
    del asr
    # WhisperX's Korean CTC model aligns supplied text, not a rewritten transcript.
    name = os.environ.get('SERYU_ALIGN_MODEL', 'kresnik/wav2vec2-large-xlsr-korean')
    model_a, metadata = whisperx.load_align_model(language_code='ko', device=device,
                                                 model_name=name, model_dir=cache, model_cache_only=local_only)
    waveform = whisperx.load_audio(str(audio))
    duration = len(waveform) / 16000
    for row in rows:
        if row[0] is None:
            continue
        segment = {'start': max(0, row[0] - 0.15), 'end': min(duration, row[1] + 0.15), 'text': row[2]}
        result = whisperx.align([segment], model_a, metadata, waveform, device,
                                return_char_alignments=True, interpolate_method='ignore')
        chars = [char for seg in result.get('segments', []) for char in seg.get('chars', [])
                 if normalized(char.get('char', ''))]
        timed = [c for c in chars if c.get('start') is not None and c.get('end') is not None]
        coverage = len(timed) / max(1, len(normalized(row[2])))
        if not timed or coverage < 0.6:
            row[0:2] = [None, None]
            row[4] = '강제 정렬 실패 · 수동 입력 필요'
            continue
        row[0], row[1] = float(timed[0]['start']), float(timed[-1]['end'])
        row[3] = round(min(row[3], coverage, sum(c.get('score', 0) for c in timed)/len(timed)), 3)
        row[4] = '저신뢰 · 수정 필요' if row[3] < 0.85 else '자동 후보 · 확인 필요'
    # No times are filled across missing lines or instrumental gaps.
    return {'rows': rows, 'backend': 'faster-whisper + WhisperX ko CTC',
            'model': model, 'alignment_model': name, 'device': device,
            'warning': '한국어 노래 적합성은 실제 음원으로 검토해야 합니다. 자동 결과를 청취·확인하세요.'}


if __name__ == '__main__':
    try:
        target = Path(sys.argv[3])
        if target.exists():
            raise ValueError('기존 정렬 결과를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.')
        result = align(Path(sys.argv[1]), Path(sys.argv[2]).read_text(encoding='utf-8-sig'),
                       model=os.environ.get('SERYU_ASR_MODEL', 'small'),
                       device=os.environ.get('SERYU_AI_DEVICE', 'cpu'), cache=os.environ.get('SERYU_MODEL_CACHE'))
        with target.open('x', encoding='utf-8') as output:
            output.write(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(f'가사 정렬 실패: {exc}. 가짜 타이밍은 생성하지 않습니다. 수동 SRT를 사용하세요.', file=sys.stderr)
        sys.exit(1)
