"""Local, deterministic music mastering and lyric-video pipeline."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

SIZES = {'youtube': (1920, 1080), 'shorts': (1080, 1920)}


def run(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed:\n{result.stderr[-6000:]}')
    return result


def ff(*args):
    return run(['ffmpeg', '-hide_banner', '-nostdin', '-y', *args])


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams',
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
    for i, item in enumerate(c['media']):
        frames = (total_frames * (i + 1) // len(c['media'])) - (total_frames * i // len(c['media']))
        part = work / f'{kind}-{i}.mp4'
        inputs = ['-loop', '1'] if item['type'] == 'image' else ['-stream_loop', '-1']
        ff(*inputs, '-i', item['path'], '-an', '-vf',
           f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps},format=yuv420p',
           '-frames:v', frames, '-c:v', 'libx264', '-preset', c['preset'], '-crf', '20', '-threads', c['threads'], part)
        parts.append(part)
    listing = work / 'concat.txt'
    listing.write_text(''.join(f"file '{p.name}'\n" for p in parts))
    joined = work / f'{kind}-joined.mp4'
    ff('-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', joined)
    # Simple filenames and cwd keep FFmpeg filter escaping independent of user paths.
    shutil.copyfile(c['lyrics'], work / 'lyrics.srt')
    shutil.copyfile(c['logo'], work / 'logo.png')
    (work / 'next.txt').write_text(str(c['next_song']['text']), encoding='utf-8')
    size = 42 if kind == 'youtube' else 48
    graph = (f'[1:v]scale={width // 7}:-1[logo];[0:v][logo]overlay=W-w-30:30:shortest=1,'
             f"subtitles=lyrics.srt:force_style='FontName={c['font']},FontSize=18,Alignment=2,MarginV=32',"
             f"drawtext=font='{c['font']}':textfile=next.txt:expansion=none:fontsize={size}:fontcolor=white:"
             f"box=1:boxcolor=black@0.65:boxborderw=12:x=(w-tw)/2:y=h*0.18:enable='gte(t,{max(0, duration-c['next_song']['seconds'])})'[v]")
    args = ['ffmpeg', '-hide_banner', '-nostdin', '-y', '-i', joined, '-loop', '1', '-i', 'logo.png', '-i', audio,
            '-filter_complex_threads', '1', '-filter_complex', graph, '-map', '[v]', '-map', '2:a:0',
            '-t', str(duration), '-c:v', 'libx264', '-preset', c['preset'], '-crf', '20', '-pix_fmt', 'yuv420p',
            '-threads', str(c['threads']), '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]
    result = subprocess.run([str(a) for a in args], cwd=work, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-6000:])
    return probe(target)


def build(config):
    environment = check_environment()
    c = load_config(config)
    c['output_dir'].mkdir(parents=True, exist_ok=True)
    outputs = [c['output_dir'] / f'{f}.mp4' for f in c['formats']]
    outputs += [c['output_dir'] / 'mastered.wav', c['output_dir'] / 'report.json']
    if any(p.exists() for p in outputs):
        raise ValueError('Output already exists; choose a new output_dir to preserve previous results')
    original_hash = hashlib.sha256(c['audio'].read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='seryu-') as tmp:
        work = Path(tmp)
        audio = work / 'mastered.wav'
        loudness = master(c['audio'], audio, c['mastering'])
        duration = float(probe(audio)['format']['duration'])
        report = {'environment': environment, 'source_sha256': original_hash, 'duration': duration,
                  'loudness': loudness, 'videos': {}}
        for kind in c['formats']:
            print(f'Rendering {kind}...', flush=True)
            target = work / f'{kind}.mp4'
            info = render(c, audio, duration, kind, work, target)
            # AAC can introduce intersample peaks: measure the delivered file too.
            encoded = analyze(target, c['mastering'])
            if float(encoded['input_tp']) >= 0:
                raise RuntimeError('AAC output may clip; lower true_peak_db and retry')
            report['videos'][kind] = {'probe': info, 'loudness': encoded}
        if hashlib.sha256(c['audio'].read_bytes()).hexdigest() != original_hash:
            raise RuntimeError('Source audio changed during rendering')
        for kind in c['formats']:
            shutil.copyfile(work / f'{kind}.mp4', c['output_dir'] / f'{kind}.mp4')
        shutil.copyfile(audio, c['output_dir'] / 'mastered.wav')
        (c['output_dir'] / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
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
