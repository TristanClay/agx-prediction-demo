"""Explicit visitor storage and an instance execution lock."""
import sys,tempfile,threading
from dataclasses import dataclass
from pathlib import Path
ROOT=Path(__file__).resolve().parent
for base in ('PipeLine2','PipeLine3'): sys.path.insert(0,str(ROOT/base))
from agx_integration_v1_1.runtime import run_new,scenario_paths,save_draft
LOCK=threading.Lock()
@dataclass(frozen=True)
class Session:
    root: Path
    @property
    def runs(self): return self.root/'runs'
    @property
    def workspace(self): return self.root/'workspace'
def session_context(state):
    if '_public_session' not in state: state['_public_session']=Session(Path(tempfile.mkdtemp(prefix='agx-visitor-')))
    return state['_public_session']
def paths_for_session(ctx,scenario):
    return scenario_paths(scenario,runs_root=ctx.runs,replay_root=ROOT/'PipeLine2/runs/integration')
def assert_visible(ctx,bundle):
    p=bundle.root.resolve()
    if p not in [x.resolve() for x in paths_for_session(ctx,bundle.request['scenario'])] and not p.is_relative_to(ctx.runs.resolve()):
        raise ValueError('This run does not belong to this visitor')
def run_for_session(ctx,request):
    if not LOCK.acquire(blocking=False): raise RuntimeError('服务正在执行其他合成任务，请稍后重试。')
    try:
        if len(list(ctx.runs.glob('*/request.json')))>=5 and not (ctx.runs/request.run_id/'request.json').exists():
            raise ValueError('当前会话已达到 5 个运行上限，请下载已有结果。')
        return run_new(request,ctx.runs,ROOT/'PipeLine3')
    finally: LOCK.release()
def saved_draft(ctx,bundle,draft):
    assert_visible(ctx,bundle)
    return ctx.workspace/'drafts'/f"{draft['draft_id']}.json"
def save_for_session(ctx,bundle,draft):
    assert_visible(ctx,bundle)
    return save_draft(bundle,draft,ctx.workspace)
