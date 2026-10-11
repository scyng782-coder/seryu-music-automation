"""Retained sample E2E: never touches existing v1 assets/results."""
import argparse
from pathlib import Path
import tempfile
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from examples.generate_sample import generate
from seryu.jobs import JobStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,help='Writable parent directory outside Git')
    args=parser.parse_args()
    parent=Path(args.root).resolve(); parent.mkdir(parents=True,exist_ok=True)
    root=Path(tempfile.mkdtemp(prefix='sample-',dir=parent))
    uploads=root/'uploads'
    generate(uploads)
    store=JobStore(root,uploads)
    job,_=store.prepare('sample',uploads/'song.wav',None,[uploads/f'image{i}.png' for i in range(1,9)],
                        uploads/'clip.mp4',uploads/'logo.png',uploads/'lyrics.srt')
    output=store.render(job,job.rows,{'formats':['youtube','shorts'],'transition_seconds':.1,
                                   'subtitles':{'size':18,'position':'bottom'},
                                   'crop':{'youtube':{'x':.5,'y':.5},'shorts':{'x':.4,'y':.5}}},True,
                       lambda fraction,message: print(f'{fraction:.0%} {message}',flush=True))
    print(f'RESULT_DIR={output}')


if __name__=='__main__': main()
