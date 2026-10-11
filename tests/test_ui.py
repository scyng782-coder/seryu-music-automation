"""Authenticated real HTTP workflow; no public links or copyrighted uploads."""
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import unittest
from unittest.mock import patch

import httpx
from gradio_client import Client, handle_file

UI_TEMP = tempfile.TemporaryDirectory(prefix='seryu-ui-')
os.environ['SERYU_DATA_DIR'] = UI_TEMP.name
from app import create_ui, credentials
from examples.generate_sample import generate
from seryu.jobs import JobStore


class UITests(unittest.TestCase):
    def test_auth_upload_manual_render_and_private_files(self):
        root = Path(UI_TEMP.name)
        assets = root/'assets'
        generate(assets)
        store = JobStore(root/'jobs', root/'uploads')
        ui = create_ui(store)
        password = secrets.token_urlsafe(24)
        client = None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        try:
            _,url,_=ui.launch(server_name='127.0.0.1',server_port=port,share=False,
                              auth=('tester',password),prevent_thread_lock=True,quiet=True,
                              max_file_size='1mb',allowed_paths=[str(store.results)],blocked_paths=[str(store.private)],
                              enable_monitoring=False)
            with httpx.Client(base_url=url,trust_env=False) as anonymous:
                self.assertEqual(anonymous.get('/config').status_code,401)
                self.assertNotEqual(anonymous.post('/upload',files={'files':('a.txt',b'hello')}).status_code,200)
            client=Client(url,auth=('tester',password),verbose=False,httpx_kwargs={'trust_env':False})
            args=dict(audio=handle_file(assets/'song.wav'),txt=None,
                      images=[handle_file(assets/f'image{i}.png') for i in range(1,9)],
                      video=handle_file(assets/'clip.mp4'),logo=handle_file(assets/'logo.png'),
                      srt=handle_file(assets/'lyrics.srt'),order='')
            prepared=client.predict(**args,api_name='/prepare')
            rows=prepared[0]['data']
            preview=client.predict(rows={'headers':['시작(초)','끝(초)','가사','신뢰 지표','검토 상태'],'data':rows},
                                   line_number=1,api_name='/preview')
            self.assertTrue(preview)
            result=client.predict(**args,rows={'headers':['시작(초)','끝(초)','가사','신뢰 지표','검토 상태'],'data':rows},
                                  reviewed=True,formats=['youtube'],size=18,position='bottom',lufs=-14,peak=-2,lra=11,
                                  next_text='다음 곡: 세류',next_seconds=2,transition=0,yx=.5,yy=.5,sx=.5,sy=.5,
                                  api_name='/render')
            self.assertIn('제작 완료',result[1])
            reports=list(store.results.rglob('report.json'))
            self.assertEqual(len(reports),1)
            self.assertTrue(json.loads(reports[0].read_text())['job']['reviewed'])
            with httpx.Client(base_url=url,trust_env=False) as session:
                self.assertEqual(session.post('/login',data={'username':'tester','password':password}).status_code,200)
                private=next(store.private.rglob('audio.wav'))
                self.assertEqual(session.get('/gradio_api/file='+str(private)).status_code,403)
                self.assertEqual(session.get('/gradio_api/file='+str(reports[0])).status_code,200)
                large=session.post('/gradio_api/upload',files={'files':('large.wav',b'0'*(1024**2+1))})
                self.assertEqual(large.status_code,413)
            self.assertFalse(ui.share)
        finally:
            if client is not None:
                client.close()
            ui.close()

    def test_credentials_required(self):
        with patch.dict(os.environ,{'SERYU_UI_USER':'','SERYU_UI_PASSWORD':''}):
            with self.assertRaises(ValueError): credentials()
        with patch.dict(os.environ,{'SERYU_UI_USER':'tester','SERYU_UI_PASSWORD':'short'}):
            with self.assertRaises(ValueError): credentials()
        with patch.dict(os.environ,{'SERYU_UI_USER':'세류','SERYU_UI_PASSWORD':'테스트용 긴 비밀번호 123456'}):
            auth=credentials()
            self.assertTrue(auth('세류','테스트용 긴 비밀번호 123456'))
            self.assertFalse(auth('다른 사용자','테스트용 긴 비밀번호 123456'))


if __name__=='__main__': unittest.main()
