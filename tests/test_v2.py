import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from main import Cancelled, _cancel, run, probe
from examples.generate_sample import generate
from seryu.jobs import JobStore, sha256, safe_probe
from seryu.lyrics import anchor_lines, lyric_lines, parse_srt, to_srt, validate_rows


class LyricsTests(unittest.TestCase):
    def test_korean_repetitions_and_instrumental_gaps(self):
        lines = lyric_lines('[Verse]\n너와 함께\n[간주]\n오늘 밤\n너와 함께')
        words = [dict(word=text, start=start, end=start+1, probability=.96)
                 for text,start in [('너와 함께',1),('오늘 밤',5),('너와 함께',10)]]
        rows = anchor_lines(lines, words)
        self.assertEqual([r[2] for r in rows], ['너와 함께','오늘 밤','너와 함께'])
        self.assertEqual([r[0] for r in rows], [1,5,10])
        self.assertEqual(parse_srt(to_srt(rows,12),12)[2][2], '너와 함께')
        missing = anchor_lines(['가나다라마바사'], [])
        self.assertIsNone(missing[0][0])
        with self.assertRaises(ValueError): to_srt(missing,12)

    def test_reject_bad_times_and_control_text(self):
        for rows in ([[0,2,'가'],[1,3,'나']], [[float('nan'),1,'가']], [[0,2,'{\\pos(0,0)}가']], [[0,13,'가']]):
            with self.assertRaises(ValueError): validate_rows(rows,12)
        with self.assertRaises(ValueError): parse_srt('1\n00:70:00,000 --> 00:70:01,000\n가',5000)


class JobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='seryu-v2-tests-')
        cls.root = Path(cls.tmp.name)
        cls.uploads = cls.root/'uploads'
        generate(cls.uploads)
        cls.store = JobStore(cls.root/'data',cls.uploads)

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def prepare(self, owner='test'):
        p = self.uploads
        return self.store.prepare(owner, p/'song.wav', None, [p/f'image{i}.png' for i in range(1,9)],
                                  p/'clip.mp4',p/'logo.png',p/'lyrics.srt')[0]

    def test_paths_limits_order_and_owner(self):
        with self.assertRaises(ValueError): self.store.uploaded('/etc/passwd',{'.wav'})
        link = self.uploads/'linked.wav'
        link.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): self.store.uploaded(link,{'.wav'})
        job = self.prepare()
        with self.assertRaises(ValueError): self.store.get(job.id,'someone-else')
        p = self.uploads
        with self.assertRaises(ValueError): self.store.prepare('test',p/'song.wav',None,[p/f'image{i}.png' for i in range(1,9)],None,p/'logo.png',p/'lyrics.srt','1,1,2,3,4,5,6,7')
        tiny = JobStore(self.root/'tiny',self.uploads,max_file_mb=.001)
        with self.assertRaises(ValueError): tiny.uploaded(p/'song.wav',{'.wav'})
        disguised = self.uploads/'disguised.mp4'
        disguised.write_text('#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5\nfile:///etc/passwd\n')
        with self.assertRaises(RuntimeError): safe_probe(disguised)

    def test_render_transitions_rerun_and_preservation(self):
        job = self.prepare()
        original = sha256(self.uploads/'song.wav')
        options = {'formats':['youtube','shorts'], 'preset':'ultrafast', 'transition_seconds':.1,
                   'crop':{'youtube':{'x':0,'y':1},'shorts':{'x':1,'y':0}},
                   'subtitles':{'size':20,'position':'middle'}}
        with self.assertRaises(ValueError): self.store.render(job,job.rows,options,False)
        progress = []
        first = self.store.render(job,job.rows,options,True,lambda f,m:progress.append((f,m)))
        hashes = {p.name: sha256(p) for p in first.iterdir()}
        self.assertEqual(set(hashes), {'youtube.mp4','shorts.mp4','lyrics.srt','mastered.wav','report.json'})
        report = json.loads((first/'report.json').read_text())
        self.assertEqual(report['source_sha256'],original)
        self.assertEqual(report['job']['source_sha256'],original)
        self.assertEqual(progress[-1][0],1)
        for kind,size in [('youtube',(1920,1080)),('shorts',(1080,1920))]:
            streams=report['videos'][kind]['probe']['streams']
            v=next(s for s in streams if s['codec_type']=='video')
            a=next(s for s in streams if s['codec_type']=='audio')
            self.assertEqual((v['width'],v['height']),size)
            self.assertEqual(v['pix_fmt'],'yuv420p')
            self.assertEqual(float(v['start_time']),0)
            self.assertAlmostEqual(float(v['duration']),float(a['duration']),delta=.1)
            pixels=subprocess.run(['ffmpeg','-v','error','-i',str(first/f'{kind}.mp4'),'-vf','scale=1:1',
                                   '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            self.assertGreater(sum(pixels),0)  # first frame is not a title/black screen
        self.assertEqual(probe(first/'mastered.wav')['streams'][0]['codec_name'],'pcm_s24le')
        options['formats']=['youtube']
        second = self.store.render(job,job.rows,options,True)
        self.assertNotEqual(first,second)
        self.assertEqual(hashes,{p.name:sha256(p) for p in first.iterdir()})
        self.assertEqual(original,sha256(self.uploads/'song.wav'))
        # Cancel while a real FFmpeg operation is running; previous revisions survive.
        errors=[]
        def worker():
            try:
                self.store.render(job,job.rows,options,True,lambda f,m: job.cancel.set() if f>=.1 else None)
            except Exception as exc: errors.append(exc)
        t=threading.Thread(target=worker); t.start(); t.join(20)
        self.assertFalse(t.is_alive())
        self.assertIsInstance(errors[0],Cancelled)
        self.assertEqual(hashes,{p.name:sha256(p) for p in first.iterdir()})

    def test_cancel_running_child_and_lock(self):
        job=self.prepare()
        stopped=[]
        started=threading.Event()
        def worker():
            try:
                with self.store.operation(job):
                    started.set()
                    run(['ffmpeg','-v','error','-re','-f','lavfi','-i','sine=duration=30','-f','null','-'])
            except Cancelled: stopped.append(True)
        t=threading.Thread(target=worker); t.start(); started.wait(5)
        with self.assertRaises(ValueError):
            with self.store.operation(job): pass
        self.store.cancel_job(job.id,'test')
        t.join(6)
        self.assertFalse(t.is_alive())
        self.assertTrue(stopped)
        self.assertFalse(job.busy.locked())

    def test_missing_ai_reports_failure_without_fake_times(self):
        # This test covers the absence/error contract, not real model accuracy.
        result = subprocess.run([os.sys.executable,'-m','seryu.alignment',str(self.uploads/'song.wav'),
                                  str(self.uploads/'lyrics.srt'),str(self.root/'should-not-exist.json')],
                                 capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('가짜 타이밍은 생성하지 않습니다',result.stderr)
        self.assertFalse((self.root/'should-not-exist.json').exists())


if __name__=='__main__': unittest.main()
