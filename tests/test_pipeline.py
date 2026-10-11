import hashlib
from pathlib import Path
import tempfile
import unittest
import subprocess

import yaml

from main import build, load_config, timestamp, ff, master
from examples.generate_sample import generate


class PipelineTests(unittest.TestCase):
    def test_timestamp(self):
        self.assertEqual(timestamp(3661.125), '01:01:01,125')

    def test_peak_control_and_silence(self):
        settings = {'integrated_lufs': -14, 'true_peak_db': -2, 'loudness_range': 11}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ff('-f', 'lavfi', '-i', 'aevalsrc=if(lt(mod(t\\,1)\\,0.02)\\,0.99*sin(2*PI*1000*t)\\,0.001*sin(2*PI*440*t)):s=48000:d=4',
               '-c:a', 'pcm_s24le', root / 'peaks.wav')
            result = master(root / 'peaks.wav', root / 'master.wav', settings)
            self.assertGreater(float(result['before']['input_tp']), -1)
            self.assertLessEqual(float(result['after']['input_tp']), -1.8)
            ff('-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '3', root / 'silence.wav')
            with self.assertRaisesRegex(ValueError, 'silent'):
                master(root / 'silence.wav', root / 'unused.wav', settings)

    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory(prefix='seryu-test-') as root:
            root = Path(root)
            generate(root / 'assets')
            config = yaml.safe_load(Path('config.yaml').read_text())
            for key in ('audio', 'lyrics', 'logo'):
                config[key] = str(root / 'assets' / Path(config[key]).name)
            for item in config['media']:
                item['path'] = str(root / 'assets' / Path(item['path']).name)
            config['output_dir'] = str(root / 'out')
            config['preset'] = 'ultrafast'
            path = root / 'config.yaml'
            path.write_text(yaml.safe_dump(config, allow_unicode=True))
            before = hashlib.sha256((root / 'assets/song.wav').read_bytes()).hexdigest()
            report = build(path)
            self.assertEqual(before, hashlib.sha256((root / 'assets/song.wav').read_bytes()).hexdigest())
            self.assertLessEqual(float(report['loudness']['after']['input_tp']), -1.8)
            self.assertAlmostEqual(float(report['loudness']['after']['input_i']), -14, delta=1)
            for kind, size in [('youtube', (1920, 1080)), ('shorts', (1080, 1920))]:
                data = report['videos'][kind]
                video = next(s for s in data['probe']['streams'] if s['codec_type'] == 'video')
                audio = next(s for s in data['probe']['streams'] if s['codec_type'] == 'audio')
                self.assertEqual((video['width'], video['height']), size)
                self.assertEqual(video['codec_name'], 'h264')
                self.assertEqual(audio['codec_name'], 'aac')
                self.assertAlmostEqual(float(data['probe']['format']['duration']), 6, delta=0.1)
                self.assertEqual(float(video['start_time']), 0)
                self.assertLess(float(data['loudness']['input_tp']), 0)
                # Verify the background actually changes; a static first frame is insufficient.
                pixels = []
                for second in (0, 1, 2, 3, 4, 5):
                    pixels.append(subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(second),
                        '-i', str(root / 'out' / f'{kind}.mp4'), '-vf', 'crop=2:2:0:400',
                        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                        capture_output=True, check=True).stdout[:3])
                self.assertGreater(len(set(pixels)), 4)
            self.assertTrue((root / 'out/report.json').is_file())
            with self.assertRaisesRegex(ValueError, 'Output already exists'):
                build(path)
            config['fps'] = 0
            path.write_text(yaml.safe_dump(config))
            with self.assertRaisesRegex(ValueError, 'fps'):
                load_config(path)


if __name__ == '__main__':
    unittest.main()
