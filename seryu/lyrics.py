"""Strict subtitles and conservative lyric matching; never invent missing times."""
import difflib
import math
import re
import unicodedata

from main import timestamp


def lyric_lines(text):
    lines = [line.strip() for line in text.lstrip('\ufeff').splitlines() if line.strip()]
    # Section / instrumental labels are metadata, not sung lyrics.
    lines = [s for s in lines if not re.fullmatch(r'\[[^\]]+\]', s)]
    if not lines or len(lines) > 1000 or sum(map(len, lines)) > 50000:
        raise ValueError('가사는 1~1000줄, 총 50,000자 이내로 입력해 주세요.')
    return lines


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFC', text).casefold() if c.isalnum())


def anchor_lines(lines, words):
    """Monotonic global matches preserve repetitions; unmapped lines remain untimed."""
    ref = ''.join(normalized(line) for line in lines)
    heard = ''
    times = []
    for word in words:
        chars = normalized(word['word'])
        heard += chars
        times.extend([word] * len(chars))
    mapping = {}
    for block in difflib.SequenceMatcher(None, ref, heard, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            mapping[block.a + offset] = block.b + offset
    rows = []
    cursor = 0
    for line in lines:
        chars = normalized(line)
        matched = [times[mapping[i]] for i in range(cursor, cursor + len(chars)) if i in mapping]
        cursor += len(chars)
        coverage = len(matched) / max(1, len(chars))
        confidence = min(coverage, sum(float(w.get('probability', 0)) for w in matched) / max(1, len(matched)))
        if coverage < 0.6 or not matched:
            rows.append([None, None, line, round(confidence, 3), '미정렬 · 수동 입력 필요'])
        else:
            rows.append([float(matched[0]['start']), float(matched[-1]['end']), line,
                         round(confidence, 3), '검토 필요' if confidence < 0.85 else '자동 후보 · 확인 필요'])
    return rows


def validate_rows(rows, duration):
    cleaned = []
    previous = 0
    if not rows or len(rows) > 1000:
        raise ValueError('자막은 1~1000줄이어야 합니다.')
    for i, row in enumerate(rows, 1):
        try:
            start, end = float(row[0]), float(row[1])
        except (ValueError, TypeError):
            raise ValueError(f'{i}번째 줄의 시작·끝 시간을 직접 입력해 주세요.') from None
        text = str(row[2]).strip()
        if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= duration + 0.001 or start < previous:
            raise ValueError(f'{i}번째 줄의 시간 순서·겹침·음원 길이를 확인해 주세요.')
        if round(end*1000) <= round(start*1000):
            raise ValueError(f'{i}번째 줄의 자막 길이는 1ms 이상이어야 합니다.')
        if not text or len(text) > 500 or any(c in text for c in ('{', '}', '\\', '\x00')):
            raise ValueError(f'{i}번째 줄의 가사 내용이 비어 있거나 허용하지 않는 자막 제어 문자가 있습니다.')
        previous = end
        cleaned.append([start, end, text, row[3] if len(row) > 3 else 1, row[4] if len(row) > 4 else '수동'])
    return cleaned


def to_srt(rows, duration):
    return '\n\n'.join(f'{i}\n{timestamp(r[0])} --> {timestamp(r[1])}\n{r[2]}'
                        for i, r in enumerate(validate_rows(rows, duration), 1)) + '\n'


def parse_srt(text, duration):
    rows = []
    for block in re.split(r'\n\s*\n', text.lstrip('\ufeff').strip().replace('\r\n', '\n')):
        match = re.fullmatch(r'\d+\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+)', block, re.S)
        if not match:
            raise ValueError('SRT 형식을 확인해 주세요: 번호 / 시간 / 가사 / 빈 줄 순서입니다.')
        def seconds(value):
            h, m, s, ms = map(int, re.split('[:,]', value))
            if m > 59 or s > 59:
                raise ValueError('SRT 시간 형식이 잘못되었습니다.')
            return h*3600 + m*60 + s + ms/1000
        rows.append([seconds(match[1]), seconds(match[2]), match[3], 1, '수동 SRT'])
    return validate_rows(rows, duration)
