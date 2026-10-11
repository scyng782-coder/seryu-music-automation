"""Private immutable uploads, owner-scoped cancellation, unique render revisions."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import uuid

import yaml

from main import build, probe, run, ff, _cancel, checkpoint
from seryu.lyrics import parse_srt, to_srt, lyric_lines, validate_rows


@dataclass
class Job:
    id: str
    owner: str
    private: Path
    public: Path
    duration: float
    files: dict
    source_hash: str
    rows: list = field(default_factory=list)
    alignment: dict = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)
    busy: threading.Lock = field(default_factory=threading.Lock)


class JobStore:
    def __init__(self, root, upload_root, max_file_mb=200, max_total_mb=1000):
        self.root = Path(root).resolve()
        self.upload_root = Path(upload_root).resolve()
        self.private = self.root / 'private'
        self.results = self.root / 'results'
        for p in (self.private, self.results, self.upload_root):
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_file = int(max_file_mb * 1024**2)
        self.max_total = int(max_total_mb * 1024**2)
        self.jobs = {}
        self.lock = threading.Lock()

    def uploaded(self, path, extensions):
        if not path:
            raise ValueError('필수 파일을 업로드해 주세요.')
        raw = Path(path)
        actual = raw.resolve(strict=True)
        if not actual.is_relative_to(self.upload_root) or raw.is_symlink() or not actual.is_file():
            raise ValueError('업로드 경로가 허용 범위를 벗어났습니다.')
        if actual.suffix.lower() not in extensions or actual.stat().st_size > self.max_file:
            raise ValueError('파일 확장자 또는 업로드 용량 제한을 확인해 주세요.')
        return actual

    def get(self, key, owner):
        with self.lock:
            job = self.jobs.get(key)
        if job is None or job.owner != owner:
            raise ValueError('이 세션의 작업이 아닙니다. 파일을 다시 준비해 주세요.')
        return job

    @contextmanager
    def operation(self, job):
        if not job.busy.acquire(blocking=False):
            raise ValueError('이미 진행 중인 작업입니다. 완료 또는 취소 후 다시 시도하세요.')
        job.cancel.clear()
        token = _cancel.set(job.cancel)
        try:
            yield
        finally:
            _cancel.reset(token)
            job.busy.release()

    def cancel_job(self, key, owner):
        job = self.get(key, owner)
        job.cancel.set()
        return '취소 요청됨 · 실행 중인 분석/렌더링 프로세스를 중지합니다.'

    def prepare(self, owner, audio, txt, images, video, logo, srt=None, order=''):
        if not 8 <= len(images or []) <= 40:
            raise ValueError('이미지는 8~40장 업로드해 주세요.')
        specs = [('audio', audio, {'.wav', '.mp3'}), ('logo', logo, {'.png'})]
        if txt:
            specs.append(('text', txt, {'.txt'}))
        if srt:
            specs.append(('srt', srt, {'.srt'}))
        if not txt and not srt:
            raise ValueError('UTF-8 TXT 가사 또는 수동 SRT를 업로드해 주세요.')
        for i, image in enumerate(images):
            specs.append((f'image{i+1:02}', image, {'.png', '.jpg', '.jpeg', '.webp'}))
        if video:
            specs.append(('video', video, {'.mp4'}))
        sources = [(key, self.uploaded(path, exts)) for key, path, exts in specs]
        if any(p.stat().st_size > 128*1024 for key, p in sources if key in ('text', 'srt')):
            raise ValueError('TXT/SRT는 128KB 이내로 입력해 주세요.')
        if sum(p.stat().st_size for _, p in sources) > self.max_total:
            raise ValueError('전체 업로드 용량 제한을 초과했습니다.')
        image_keys = [f'image{i+1:02}' for i in range(len(images))]
        if order.strip():
            try:
                numbers = [int(s.strip()) for s in order.split(',')]
            except ValueError:
                raise ValueError('이미지 순서는 1,2,3처럼 번호로 입력해 주세요.') from None
            if sorted(numbers) != list(range(1, len(images)+1)):
                raise ValueError('이미지 번호를 중복·누락 없이 모두 입력해 주세요.')
            image_keys = [f'image{i:02}' for i in numbers]
        key = uuid.uuid4().hex
        private = self.private / key
        private.mkdir(mode=0o700)
        files = {}
        # Copy snapshots; never render from browser cache paths or original files.
        for name, source in sources:
            target = private / (name + source.suffix.lower())
            with source.open('rb') as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            files[name] = target
        info = safe_probe(files['audio'])
        if not any(s['codec_type'] == 'audio' for s in info['streams']):
            raise ValueError('음원 파일에 오디오 스트림이 없습니다.')
        duration = float(info['format']['duration'])
        if not 1 <= duration <= 1800:
            raise ValueError('음원 길이는 1초~30분이어야 합니다.')
        for name in ['logo', *image_keys]:
            info = safe_probe(files[name])
            if not any(s.get('codec_name') in ('png', 'mjpeg', 'webp') for s in info['streams']):
                raise ValueError('이미지 파일 내용이 올바르지 않습니다.')
        if video:
            info = safe_probe(files['video'])
            if not any(s['codec_type'] == 'video' for s in info['streams']) or 'mp4' not in info['format']['format_name']:
                raise ValueError('MP4 동영상 파일 내용이 올바르지 않습니다.')
        if txt:
            text = files['text'].read_text(encoding='utf-8-sig')
            lyric_lines(text)
        rows = parse_srt(files['srt'].read_text(encoding='utf-8-sig'), duration) if srt else []
        files['image_order'] = image_keys
        job = Job(key, owner, private, self.results/key, duration, files, sha256(files['audio']), rows)
        with self.lock:
            self.jobs[key] = job
        names = '\n'.join(f'{i}: {Path(images[i-1]).name}' for i in range(1,len(images)+1))
        return job, names

    def align(self, job):
        if 'text' not in job.files:
            raise ValueError('자동 정렬에는 UTF-8 TXT 가사가 필요합니다.')
        with self.operation(job):
            result_path = job.private / f'alignment-{uuid.uuid4().hex}.json'
            interpreter = os.environ.get('SERYU_AI_PYTHON', sys.executable)
            run([interpreter, '-m', 'seryu.alignment', job.files['audio'], job.files['text'], result_path],
                cwd=Path(__file__).resolve().parents[1])
            result = json.loads(result_path.read_text(encoding='utf-8'))
            job.rows = result['rows']
            job.alignment = {k: v for k, v in result.items() if k != 'rows'}
            return job.rows

    def preview(self, job, rows, line_number):
        if int(line_number) != line_number or not 1 <= line_number <= len(rows):
            raise ValueError('미리 들을 자막 줄 번호를 확인해 주세요.')
        row = validate_rows([rows[int(line_number)-1]], job.duration)[0]
        with self.operation(job):
            folder = job.public / f'preview-{uuid.uuid4().hex}'
            folder.mkdir(parents=True)
            target = folder / 'line.wav'
            ff('-ss', row[0], '-i', job.files['audio'], '-t', row[1]-row[0], '-vn', '-c:a', 'pcm_s16le', target)
            return target

    def render(self, job, rows, settings, reviewed, progress=None):
        if set(settings) - {'formats', 'subtitles', 'mastering', 'next_song', 'transition_seconds', 'crop', 'preset'}:
            raise ValueError('허용하지 않는 작업 설정입니다.')
        if not reviewed:
            raise ValueError('자동 타이밍 또는 수동 자막을 청취·확인한 뒤 확인란을 선택해 주세요.')
        with self.operation(job):
            rows = validate_rows(rows, job.duration)
            # TXT correspondence is strict; same repeated chorus rows remain in order.
            if 'text' in job.files and [r[2] for r in rows] != lyric_lines(job.files['text'].read_text(encoding='utf-8-sig')):
                raise ValueError('자막의 줄 순서·반복 후렴을 TXT 가사와 동일하게 유지해 주세요.')
            revision = uuid.uuid4().hex
            private = job.private / revision
            private.mkdir(mode=0o700)
            subtitle = private / 'lyrics.srt'
            subtitle.write_text(to_srt(rows, job.duration), encoding='utf-8')
            output = job.public / revision
            config = {'audio': str(job.files['audio']), 'lyrics': str(subtitle), 'logo': str(job.files['logo']),
                      'media': [{'path': str(job.files[k]), 'type': 'image'} for k in job.files['image_order']],
                      'output_dir': str(output), 'formats': ['youtube', 'shorts'], 'fps': 30, 'threads': 2,
                      'preset': 'fast', 'mastering': {'integrated_lufs': -14, 'true_peak_db': -2, 'loudness_range': 11},
                      'font': 'Noto Sans CJK KR', 'next_song': {'text': '다음 곡도 함께해 주세요', 'seconds': 5}}
            config.update(settings)
            if 'video' in job.files:
                config['media'].insert(len(config['media'])//2, {'path': str(job.files['video']), 'type': 'video'})
            config_path = private / 'config.yaml'
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            try:
                report = build(config_path, progress=progress, cancel=job.cancel)
                checkpoint()
                if sha256(job.files['audio']) != job.source_hash:
                    raise RuntimeError('업로드 음원 해시가 변경되었습니다.')
                report['job'] = {'id': job.id, 'revision': revision, 'reviewed': True,
                                 'alignment': job.alignment or {'backend': 'manual SRT'},
                                 'rows': rows, 'source_sha256': job.source_hash,
                                 'listening_review_required': True}
                # Only this newly generated report receives UI review metadata.
                (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                job.rows = rows
                return output
            except BaseException:
                # Retain diagnostics and any newly produced files. Never erase previous revisions.
                if output.exists():
                    (output/'INCOMPLETE.txt').write_text('제작 실패 또는 취소된 작업입니다. 완성 결과로 사용하지 마세요.', encoding='utf-8')
                raise


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_probe(path):
    # Force demuxers before inspecting untrusted files. A disguised playlist must
    # never make FFmpeg read local arbitrary paths or contact remote hosts.
    format_name = {'.wav': 'wav', '.mp3': 'mp3', '.mp4': 'mov',
                   '.png': 'image2', '.jpg': 'image2', '.jpeg': 'image2', '.webp': 'image2'}[path.suffix]
    args = ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', format_name]
    if format_name == 'mov':
        args += ['-enable_drefs', '0', '-use_absolute_path', '0']
    return json.loads(run([*args, '-show_format', '-show_streams', '-of', 'json', path]).stdout)
