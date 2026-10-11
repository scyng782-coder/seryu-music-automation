"""Authenticated Gradio UI; never creates public share links."""
import argparse
import hmac
import os
from pathlib import Path

# Set before importing Gradio; keep uploads and jobs outside the Git checkout.
DATA_ROOT = Path(os.environ.get('SERYU_DATA_DIR', str(Path.home() / 'seryu-data'))).resolve()
UPLOAD_ROOT = DATA_ROOT / 'uploads'
os.environ['GRADIO_TEMP_DIR'] = str(UPLOAD_ROOT)
os.environ.setdefault('GRADIO_ANALYTICS_ENABLED', 'False')

import gradio as gr

from main import check_environment, Cancelled
from seryu.jobs import JobStore


def credentials():
    user = os.environ.get('SERYU_UI_USER')
    password = os.environ.get('SERYU_UI_PASSWORD')
    if not user or not password or len(password) < 12:
        raise ValueError('SERYU_UI_USER와 12자 이상의 SERYU_UI_PASSWORD를 환경 변수로 설정하세요. 비밀번호를 코드에 저장하지 마세요.')
    return lambda supplied_user, supplied_password: (hmac.compare_digest(supplied_user.encode('utf-8'), user.encode('utf-8')) and
                                                     hmac.compare_digest(supplied_password.encode('utf-8'), password.encode('utf-8')))


def create_ui(store):
    os.environ['GRADIO_TEMP_DIR'] = str(store.upload_root)
    def owner(request):
        if not request.session_hash:
            raise ValueError('세션이 없습니다. 브라우저를 새로고침하세요.')
        return request.session_hash

    def prepare_state(audio, txt, images, video, logo, srt, order, request):
        job, names = store.prepare(owner(request), audio, txt, images, video, logo, srt, order)
        return job, {'key': job.id, 'signature': [audio, txt, images, video, logo, srt, order]}, names

    def prepare(audio, txt, images, video, logo, srt, order, request: gr.Request):
        try:
            job, state, names = prepare_state(audio, txt, images, video, logo, srt, order, request)
            return state, job.rows, names, f'입력 준비 완료 · 음원 {job.duration:.2f}초. 자막을 검토하거나 자동 정렬하세요.', False
        except Exception as exc:
            raise gr.Error(str(exc)) from exc

    def align(state, request: gr.Request, progress=gr.Progress()):
        try:
            job = store.get((state or {}).get('key'), owner(request))
            progress(0.1, desc='한국어 보컬 인식 및 가사 강제 정렬')
            rows = store.align(job)
            return rows, '정렬 후보가 준비됐습니다. 저신뢰·미정렬 줄을 수정하고 오디오를 청취해 주세요.', False
        except Exception as exc:
            raise gr.Error(str(exc)) from exc

    def ensure(audio, txt, images, video, logo, srt, order, state, rows, request: gr.Request):
        try:
            signature = [audio, txt, images, video, logo, srt, order]
            if not state or state.get('signature') != signature:
                job, state, names = prepare_state(audio, txt, images, video, logo, srt, order, request)
                return state, job.rows, names
            return state, rows, gr.skip()
        except Exception as exc:
            raise gr.Error(str(exc)) from exc

    def start(audio, txt, images, video, logo, srt, order, state, rows, reviewed,
              formats, size, position, lufs, peak, lra, next_text, next_seconds,
              transition, yx, yy, sx, sy, request: gr.Request, progress=gr.Progress()):
        try:
            signature = [audio, txt, images, video, logo, srt, order]
            new_job = not state or state.get('signature') != signature
            if new_job:
                job, state, _ = prepare_state(audio, txt, images, video, logo, srt, order, request)
                rows = job.rows
            else:
                job = store.get(state['key'], owner(request))
            if not rows:
                progress(0.05, desc='가사 자동 정렬 · 실패 시 수동 SRT를 사용해 주세요')
                rows = store.align(job)
                return state, rows, '가사 정렬 후보 완료. 타이밍을 확인·수정한 뒤 제작 시작을 다시 눌러 주세요.', None, None, [], False
            if not formats:
                raise ValueError('출력 형식을 하나 이상 선택하세요.')
            # Uploaded manual SRT can render in one click after user approval.
            settings = {'formats': formats, 'subtitles': {'size': int(size), 'position': position},
                        'mastering': {'integrated_lufs': lufs, 'true_peak_db': peak, 'loudness_range': lra},
                        'next_song': {'text': next_text, 'seconds': next_seconds},
                        'transition_seconds': transition,
                        'crop': {'youtube': {'x': yx, 'y': yy}, 'shorts': {'x': sx, 'y': sy}}}
            output = store.render(job, rows, settings, reviewed,
                                  progress=lambda fraction, message: progress(fraction, desc=message))
            youtube = str(output/'youtube.mp4') if 'youtube' in formats else None
            shorts = str(output/'shorts.mp4') if 'shorts' in formats else None
            files = [str(p) for p in sorted(output.iterdir()) if p.suffix in ('.mp4', '.wav', '.srt', '.json')]
            return state, rows, '제작 완료 · 원본 보존. 음질과 자막을 확인한 뒤 다운로드하세요.', youtube, shorts, files, reviewed
        except Cancelled as exc:
            raise gr.Error(str(exc)) from exc
        except Exception as exc:
            raise gr.Error(f'제작 실패: {exc}') from exc

    def cancel(state, request: gr.Request):
        try:
            return store.cancel_job((state or {}).get('key'), owner(request))
        except Exception as exc:
            raise gr.Error(str(exc)) from exc

    def preview(state, rows, line_number, request: gr.Request):
        try:
            job = store.get((state or {}).get('key'), owner(request))
            return str(store.preview(job, rows, line_number))
        except Exception as exc:
            raise gr.Error(str(exc)) from exc

    with gr.Blocks(title='세류 음악 자동화 2.0') as demo:
        gr.Markdown('# 세류 음악 자동화 2.0\n음악과 영상은 0초부터 시작합니다. 원본·기존 결과는 보존합니다.\n'
                    '자동 가사 정렬은 실험 기능이며 한국어 노래 타이밍 검토가 필수입니다. API 키는 필요 없습니다.')
        state = gr.State()
        with gr.Row():
            audio = gr.File(label='Suno 음원 WAV / MP3', file_types=['.wav', '.mp3'])
            txt = gr.File(label='UTF-8 가사 TXT (줄 순서·반복 후렴 유지)', file_types=['.txt'])
            srt = gr.File(label='수동 SRT (AI 없이 제작 가능)', file_types=['.srt'])
        listen = gr.Audio(label='업로드 음원 청취', type='filepath', interactive=False)
        audio.change(lambda value: value, audio, listen, api_name=False)
        images = gr.File(label='이미지 8~40장 · 드래그로 순서 변경', file_count='multiple',
                         file_types=['.png', '.jpg', '.jpeg', '.webp'], allow_reordering=True)
        with gr.Row():
            video = gr.File(label='선택 MP4 · 이미지 중간에 삽입', file_types=['.mp4'])
            logo = gr.File(label='세류 PNG 로고', file_types=['.png'])
        order = gr.Textbox(label='선택: 이미지 순서 번호 (예: 3,1,2,4,5,6,7,8)', placeholder='비우면 업로드 표시 순서')
        names = gr.Textbox(label='준비된 이미지 번호', interactive=False)
        with gr.Row():
            prepare_button = gr.Button('입력 준비 / 자막 불러오기')
            align_button = gr.Button('가사 자동 정렬 (선택)')
            cancel_button = gr.Button('현재 작업 취소', variant='stop')
        rows = gr.Dataframe(headers=['시작(초)', '끝(초)', '가사', '신뢰 지표', '검토 상태'],
                            datatype=['number','number','str','number','str'], type='array',
                            interactive=True, static_columns=[3,4], label='줄별 타이밍 수정 · 빈 시간은 직접 입력', wrap=True)
        with gr.Row():
            line_number = gr.Number(value=1, precision=0, label='미리 들을 자막 줄 번호')
            preview_button = gr.Button('선택 줄 타이밍 청취')
            line_audio = gr.Audio(label='선택한 자막 구간 오디오', type='filepath', interactive=False)
        reviewed = gr.Checkbox(label='가사 순서·반복 후렴·타이밍을 청취하여 확인했습니다', value=False)
        with gr.Accordion('출력·마스터링·자막 설정', open=True):
            formats = gr.CheckboxGroup(['youtube', 'shorts'], value=['youtube','shorts'], label='출력 형식')
            with gr.Row():
                size = gr.Slider(8,60,value=18,step=1,label='자막 크기 (ASS 기준)')
                position = gr.Dropdown([('아래','bottom'),('중앙','middle'),('위','top')], value='bottom',label='자막 위치')
            with gr.Row():
                lufs = gr.Slider(-24,-8,value=-14,step=0.5,label='목표 LUFS')
                peak = gr.Slider(-9,-1,value=-2,step=0.5,label='최대 true peak dBTP')
                lra = gr.Slider(1,20,value=11,step=1,label='LRA 목표')
            next_text = gr.Textbox(value='다음 곡도 함께해 주세요',label='끝부분 다음 곡 안내')
            next_seconds = gr.Slider(1,15,value=5,step=1,label='다음 곡 안내 시간 (초)')
            transition = gr.Slider(0,2,value=0,step=0.1,label='자연스러운 전환 (초, 0은 바로 전환)')
            gr.Markdown('크롭 위치: 0은 왼쪽/위, 0.5는 중앙, 1은 오른쪽/아래입니다. 긴 문구는 줄바꿈으로 조절하세요.')
            with gr.Row():
                yx = gr.Slider(0,1,value=0.5,label='16:9 가로 위치')
                yy = gr.Slider(0,1,value=0.5,label='16:9 세로 위치')
                sx = gr.Slider(0,1,value=0.5,label='9:16 가로 위치')
                sy = gr.Slider(0,1,value=0.5,label='9:16 세로 위치')
        start_button = gr.Button('제작 시작 / 수정 후 새 결과 제작', variant='primary')
        status = gr.Textbox(label='진행 결과', interactive=False)
        with gr.Row():
            youtube = gr.Video(label='유튜브 16:9 미리보기')
            shorts = gr.Video(label='쇼츠 9:16 미리보기')
        downloads = gr.File(file_count='multiple', label='완성 결과 다운로드', interactive=False)
        gr.Markdown('기본 공개 접속 비활성화 · 인증 필수 · 한 계정으로 공유하는 개인용 서버입니다.\n'
                    '마스터링 후 반드시 청감 검증하세요. 이미 왜곡된 원본의 복구나 전문 엔지니어의 판단을 대신하지 않습니다.')
        inputs = [audio,txt,images,video,logo,srt,order]
        prepare_button.click(prepare, inputs, [state,rows,names,status,reviewed], concurrency_id='work', concurrency_limit=1, api_name='prepare')
        align_button.click(align, state, [rows,status,reviewed], concurrency_id='work', concurrency_limit=1, api_name='align')
        preview_button.click(preview, [state,rows,line_number],line_audio,concurrency_id='work',concurrency_limit=1,api_name='preview')
        ready = start_button.click(ensure, [*inputs,state,rows], [state,rows,names],
                                   concurrency_id='work', concurrency_limit=1, api_name=False)
        ready.success(start, [*inputs,state,rows,reviewed,formats,size,position,lufs,peak,lra,next_text,next_seconds,transition,yx,yy,sx,sy],
                           [state,rows,status,youtube,shorts,downloads,reviewed], concurrency_id='work', concurrency_limit=1, api_name='render')
        cancel_button.click(cancel,state,status,queue=False,api_name='cancel')
    demo.queue(default_concurrency_limit=1, max_size=8)
    return demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lan', action='store_true', help='명시적으로 LAN 인터페이스를 사용 (TLS 프록시/신뢰 네트워크 권장)')
    parser.add_argument('--port', type=int, default=7860)
    args = parser.parse_args()
    if DATA_ROOT.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError('음원과 결과를 Git 저장소 안에 보관하지 않도록 SERYU_DATA_DIR을 저장소 밖으로 지정하세요.')
    auth = credentials()
    check_environment()
    store = JobStore(DATA_ROOT, UPLOAD_ROOT)
    demo = create_ui(store)
    demo.launch(server_name='0.0.0.0' if args.lan else '127.0.0.1', server_port=args.port,
                share=False, auth=auth, auth_message='세류 음악 자동화 · 설정된 계정으로 로그인하세요.',
                max_file_size='200mb', allowed_paths=[str(store.results)], blocked_paths=[str(store.private)],
                enable_monitoring=False, show_error=False, footer_links=[], strict_cors=True)


if __name__ == '__main__':
    main()
