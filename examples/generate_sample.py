"""Generate an eight-image + video demonstration without external services."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import ff, check_environment


def generate(dest):
    check_environment()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise ValueError('Sample directory must be empty; existing assets are preserved')
    ff('-f', 'lavfi', '-i', 'sine=frequency=440:duration=6:sample_rate=48000',
       '-af', 'volume=0.5', '-c:a', 'pcm_s24le', dest / 'song.wav')
    for i, color in enumerate(['navy', 'purple', 'teal', 'maroon', 'darkgreen', 'indigo', 'darkblue', 'darkred'], 1):
        ff('-f', 'lavfi', '-i', f'color=c={color}:s=640x360', '-frames:v', '1',
           '-threads', '1', dest / f'image{i}.png')
    ff('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=1',
       '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', dest / 'clip.mp4')
    ff('-f', 'lavfi', '-i', 'color=c=black@0.0:s=360x100,format=rgba',
       '-vf', "drawtext=font='Noto Sans CJK KR':text='세류 SERYU':fontcolor=white:fontsize=42:x=10:y=20",
       '-frames:v', '1', '-threads', '1', dest / 'logo.png')
    (dest / 'lyrics.srt').write_text('1\n00:00:00,000 --> 00:00:03,000\n세류의 노래가 시작돼요\n\n2\n00:00:03,000 --> 00:00:06,000\n오늘도 함께 걸어요\n', encoding='utf-8')


if __name__ == '__main__':
    generate(Path(__file__).resolve().parent / 'generated')
