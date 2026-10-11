"""Local, deterministic music mastering and lyric-video pipeline."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from contextvars import ContextVar

_cancel = ContextVar('cancel', default=None)


class Cancelled(RuntimeError):
    pass


def checkpoint():
    event = _cancel.get()
    if event is not None and event.is_set():
        raise Cancelled('작업이 취소되었습니다. 원본과 기존 결과는 보존됩니다.')

import yaml

SIZES = {'youtube': (1920, 1080), 'shorts': (1080, 1920)}


def run(args, cwd=None):
    checkpoint()
    process = subprocess.Popen([str(a) for a in args], cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=os.name == 'posix')
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                checkpoint()
    except BaseException:
        def stop(force=False):
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            else:
                process.kill() if force else process.terminate()
        stop()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            stop(force=True)
            process.communicate()
        raise
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed:\n{result.stderr[-6000:]}')
    return result


def ff(*args):
    return run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe', *args])


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_format', '-show_streams',
                           '-of', 'json', path]).stdout)


def check_environment():
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            raise RuntimeError(f'{tool} is missing; install FFmpeg first')
    filters = run(['ffmpeg', '-hide_banner', '-filters']).stdout
    for name in ('loudnorm', 'subtitles', 'overlay', 'drawtext'):
        if not re.search(r'\b' + name + r'\b', filters):
            raise RuntimeError(f'FFmpeg filter missing: {name}')
    if 'libx264' not in run(['ffmpeg', '-hide_banner', '-encoders']).stdout:
        raise RuntimeError('FFmpeg libx264 encoder is required')
    return {'python': sys.version.split()[0], 'ffmpeg': run(['ffmpeg', '-version']).stdout.splitlines()[0]}


def analyze(audio, settings):
    filt = f"loudnorm=I={settings['integrated_lufs']}:TP={settings['true_peak_db']}:LRA={settings['loudness_range']}:print_format=json"
    result = ff('-i', audio, '-vn', '-af', filt, '-f', 'null', '-')
    matches = re.findall(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not matches:
        raise RuntimeError('No loudness measurement returned')
    data = json.loads(matches[-1])
    if not all(math.isfinite(float(data[k])) for k in ('input_i', 'input_tp', 'input_lra', 'input_thresh')):
        raise ValueError('Audio is silent or cannot be measured')
    return data


def master(audio, dest, settings):
    measured = analyze(audio, settings)
    filt = (f"loudnorm=I={settings['integrated_lufs']}:TP={settings['true_peak_db']}:"
            f"LRA={settings['loudness_range']}:measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true")
    ff('-i', audio, '-vn', '-af', filt, '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', dest)
    after = analyze(dest, settings)
    if float(after['input_tp']) > settings['true_peak_db'] + 0.2:
        raise RuntimeError('Mastered audio exceeded the configured true peak ceiling')
    return {'before': measured, 'after': after}


def positive(value, label):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label} must be a positive number')
    return value


def load_config(path):
    path = Path(path).resolve()
    c = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(c, dict):
        raise ValueError('Configuration must be a mapping')
    def asset(name):
        p = (path.parent / name).resolve()
        if not p.is_file():
            raise ValueError(f'Input file missing: {p}')
        return p
    for key in ('audio', 'lyrics', 'logo'):
        c[key] = asset(c[key])
    if not c.get('media'):
        raise ValueError('At least one media item is required')
    for item in c['media']:
        item['path'] = asset(item['path'])
        if item.get('type') not in ('image', 'video'):
            raise ValueError('Media type must be image or video')
    c['formats'] = c.get('formats', ['youtube', 'shorts'])
    if not c['formats'] or any(f not in SIZES for f in c['formats']):
        raise ValueError('Formats must contain youtube and/or shorts')
    c['output_dir'] = (path.parent / c.get('output_dir', 'output')).resolve()
    for key, default in [('fps', 30), ('threads', 2)]:
        c[key] = positive(c.get(key, default), key)
        if int(c[key]) != c[key] or c[key] > (60 if key == 'fps' else 32):
            raise ValueError(f'{key} is out of range')
    c.setdefault('mastering', {})
    for key, default, bounds in [('integrated_lufs', -14, (-70, -5)), ('true_peak_db', -2, (-9, -1)), ('loudness_range', 11, (1, 50))]:
        v = c['mastering'].setdefault(key, default)
        if not isinstance(v, (int, float)) or not bounds[0] <= v <= bounds[1]:
            raise ValueError(f'Invalid mastering setting: {key}')
    c.setdefault('next_song', {'text': '다음 곡도 함께해 주세요', 'seconds': 5})
    positive(c['next_song']['seconds'], 'next_song.seconds')
    c.setdefault('preset', 'fast')
    c.setdefault('font', 'Noto Sans CJK KR')
    if not re.fullmatch(r'[\w -]+', c['font']):
        raise ValueError('Font name contains unsupported characters')
    c.setdefault('subtitles', {'size': 18, 'position': 'bottom'})
    if not 8 <= c['subtitles'].get('size', 18) <= 60 or c['subtitles'].get('position', 'bottom') not in ('bottom', 'middle', 'top'):
        raise ValueError('Invalid subtitle size or position')
    c.setdefault('crop', {})
    for kind in SIZES:
        crop = c['crop'].setdefault(kind, {'x': 0.5, 'y': 0.5})
        if any(not isinstance(crop.get(k), (int, float)) or not 0 <= crop[k] <= 1 for k in ('x', 'y')):
            raise ValueError('Crop positions must be between 0 and 1')
    c.setdefault('transition_seconds', 0)
    if not isinstance(c['transition_seconds'], (int, float)) or not 0 <= c['transition_seconds'] <= 2:
        raise ValueError('Transition must be between 0 and 2 seconds')
    return c


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def render(c, audio, duration, kind, work, target):
    width, height = SIZES[kind]
    fps = c['fps']
    total_frames = round(duration * fps)
    if total_frames < len(c['media']):
        raise ValueError('Audio is too short for the selected media count')
    parts = []
    base_frames = [(total_frames * (i + 1) // len(c['media'])) - (total_frames * i // len(c['media'])) for i in range(len(c['media']))]
    transition = round(c.get('transition_seconds', 0) * fps) / fps
    if transition and transition >= min(base_frames) / fps / 2:
        raise ValueError('전환 길이는 가장 짧은 미디어 구간의 절반보다 짧아야 합니다.')
    crop = c.get('crop', {}).get(kind, {'x': 0.5, 'y': 0.5})
    for i, item in enumerate(c['media']):
        frames = base_frames[i] + (round(transition * fps) if i < len(c['media']) - 1 else 0)
        part = work / f'{kind}-{i}.mp4'
        inputs = ['-loop', '1'] if item['type'] == 'image' else ['-stream_loop', '-1']
        ff(*inputs, '-i', item['path'], '-an', '-vf',
           f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-ow)*{crop['x']}:(ih-oh)*{crop['y']},setsar=1,fps={fps},format=yuv420p",
           '-frames:v', frames, '-c:v', 'libx264', '-preset', c['preset'], '-crf', '20', '-threads', c['threads'], part)
        parts.append(part)
    listing = work / 'concat.txt'
    listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
    joined = work / f'{kind}-joined.mp4'
    if transition and len(parts) > 1:
        inputs = [arg for p in parts for arg in ('-threads', c['threads'], '-i', p)]
        graph_parts = [f'[{i}:v]setpts=PTS-STARTPTS,fps={fps},settb=AVTB[v{i}]' for i in range(len(parts))]
        last = 'v0'
        elapsed = 0
        for i in range(1, len(parts)):
            elapsed += base_frames[i - 1] / fps
            graph_parts.append(f'[{last}][v{i}]xfade=transition=fade:duration={transition}:offset={elapsed},fps={fps},settb=AVTB[f{i}]')
            last = f'f{i}'
        ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', ';'.join(graph_parts),
           '-map', f'[{last}]', '-frames:v', total_frames, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
           '-preset', c['preset'], '-threads', c['threads'], joined)
    else:
        ff('-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', joined)
    # Simple filenames and cwd keep FFmpeg filter escaping independent of user paths.
    shutil.copyfile(c['lyrics'], work / 'lyrics.srt')
    shutil.copyfile(c['logo'], work / 'logo.png')
    (work / 'next.txt').write_text(str(c['next_song']['text']), encoding='utf-8')
    size = 42 if kind == 'youtube' else 48
    subtitle = c.get('subtitles', {})
    alignment = {'bottom': 2, 'middle': 5, 'top': 8}[subtitle.get('position', 'bottom')]
    graph = (f'[1:v]scale={width // 7}:-1[logo];[0:v][logo]overlay=W-w-30:30:shortest=1,'
             f"subtitles=lyrics.srt:force_style='FontName={c['font']},FontSize={subtitle.get('size',18)},Alignment={alignment},MarginV=32',"
             f"drawtext=font='{c['font']}':textfile=next.txt:expansion=none:fontsize={size}:fontcolor=white:"
             f"box=1:boxcolor=black@0.65:boxborderw=12:x=(w-tw)/2:y=h*0.18:enable='gte(t,{max(0, duration-c['next_song']['seconds'])})'[v]")
    args = ['ffmpeg', '-hide_banner', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe', '-i', joined, '-loop', '1', '-i', 'logo.png', '-i', audio,
            '-filter_complex_threads', '1', '-filter_complex', graph, '-map', '[v]', '-map', '2:a:0',
            '-t', str(duration), '-c:v', 'libx264', '-preset', c['preset'], '-crf', '20', '-pix_fmt', 'yuv420p',
            '-threads', str(c['threads']), '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]
    run(args, cwd=work)
    return probe(target)


def build(config, progress=None, cancel=None):
    token = _cancel.set(cancel)
    lock = None
    try:
        c = load_config(config)
        c['output_dir'].mkdir(parents=True, exist_ok=True)
        candidate = c['output_dir'] / '.seryu.lock'
        try:
            with candidate.open('x') as marker:
                marker.write('A render owns this output directory. Do not reuse it until rendering ends.')
        except FileExistsError:
            raise ValueError('Output already exists or is being rendered; choose a new output_dir') from None
        lock = candidate
        return _build(config, progress or (lambda fraction, message: None))
    finally:
        if lock is not None:
            lock.unlink()
        _cancel.reset(token)


def _build(config, progress):
    progress(0.02, '실행 환경 확인')
    environment = check_environment()
    c = load_config(config)
    c['output_dir'].mkdir(parents=True, exist_ok=True)
    outputs = [c['output_dir'] / f'{f}.mp4' for f in c['formats']]
    outputs += [c['output_dir'] / 'mastered.wav', c['output_dir'] / 'report.json']
    outputs.append(c['output_dir'] / 'lyrics.srt')
    if any(p.exists() for p in outputs):
        raise ValueError('Output already exists; choose a new output_dir to preserve previous results')
    original_hash = hashlib.sha256(c['audio'].read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='seryu-') as tmp:
        work = Path(tmp)
        audio = work / 'mastered.wav'
        progress(0.1, '음량 분석 및 마스터링')
        loudness = master(c['audio'], audio, c['mastering'])
        duration = float(probe(audio)['format']['duration'])
        report = {'environment': environment, 'source_sha256': original_hash, 'duration': duration,
                  'loudness': loudness, 'videos': {}}
        for kind in c['formats']:
            progress(0.25 + 0.6 * c['formats'].index(kind) / len(c['formats']), f'{kind} 영상 제작')
            print(f'Rendering {kind}...', flush=True)
            target = work / f'{kind}.mp4'
            info = render(c, audio, duration, kind, work, target)
            # AAC can introduce intersample peaks: measure the delivered file too.
            encoded = analyze(target, c['mastering'])
            if float(encoded['input_tp']) >= 0:
                raise RuntimeError('AAC output may clip; lower true_peak_db and retry')
            report['videos'][kind] = {'probe': info, 'loudness': encoded}
            streams = info['streams']
            for stream in streams:
                if stream['codec_type'] in ('video', 'audio') and abs(float(stream.get('duration', duration)) - duration) > max(0.1, 2/c['fps']):
                    raise RuntimeError('오디오와 영상 길이가 일치하지 않습니다.')
        checkpoint()
        if hashlib.sha256(c['audio'].read_bytes()).hexdigest() != original_hash:
            raise RuntimeError('Source audio changed during rendering')
        for kind in c['formats']:
            with (work / f'{kind}.mp4').open('rb') as src, (c['output_dir'] / f'{kind}.mp4').open('xb') as dst:
                shutil.copyfileobj(src, dst)
        for source, name in [(audio, 'mastered.wav'), (c['lyrics'], 'lyrics.srt')]:
            with source.open('rb') as src, (c['output_dir'] / name).open('xb') as dst:
                shutil.copyfileobj(src, dst)
        report['render_settings'] = {'subtitles': c['subtitles'], 'crop': c['crop'], 'transition_seconds': c['transition_seconds']}
        with (c['output_dir'] / 'report.json').open('x', encoding='utf-8') as report_file:
            report_file.write(json.dumps(report, indent=2, ensure_ascii=False))
    progress(1, '제작 완료 · 청감과 자막 타이밍을 확인해 주세요')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--check', action='store_true', help='Check runtime without rendering')
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps(check_environment(), ensure_ascii=False, indent=2))
        else:
            build(args.config)
            print('Completed. See output_dir/report.json.')
    except (RuntimeError, ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
