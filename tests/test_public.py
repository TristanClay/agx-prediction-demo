import sys,json,hashlib
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import public_runtime as web
from agx_integration.contracts import RunRequest
from agx_integration_v1_1.runtime import open_bundle,SCENARIOS
from agx_integration_v1_1.view import next_task_drafts
from streamlit.testing.v1 import AppTest
@pytest.mark.parametrize('scenario,outcome',[('baseline','SUPPORTED'),('disagreement','SUPPORTED'),('missing','INCONCLUSIVE'),('mismatch','SUPPORTED'),('counter','REFUTED')])
def test_scenario(scenario,outcome):
 b=open_bundle(ROOT/'PipeLine2/runs/integration'/f'public-v11-{scenario}')
 f=b.result['outputs']['feedback']['feedback']
 assert f['experimental_findings']['hypothesis_outcome']==outcome
 assert f['official_publish'] is False
 assert f['gate_status']=='PENDING_GATE'
 assert b.result['actual_dft_jobs']==0
 assert f['prediction_deviation']['numeric_delta'] is None

def test_visitors_and_limit(tmp_path):
 a,b=web.Session(tmp_path/'a'),web.Session(tmp_path/'b')
 x=web.run_for_session(a,RunRequest(run_id='same'))
 y=web.run_for_session(b,RunRequest(run_id='same'))
 assert x.root!=y.root
 assert y.root not in web.paths_for_session(a,'baseline')
 draft=next_task_drafts(x)[0]
 before=(x.root/'manifest.json').read_bytes()
 assert web.save_for_session(a,x,draft).exists()
 assert not web.saved_draft(b,y,next_task_drafts(y)[0]).exists()
 with pytest.raises(ValueError): web.save_for_session(b,x,draft)
 assert (x.root/'manifest.json').read_bytes()==before
 for i in range(4):
  r=web.run_for_session(a,RunRequest(run_id=f'live{i}',mode='LIVE'))
  assert r.result['status']=='BLOCKED'
 with pytest.raises(ValueError,match='5'): web.run_for_session(a,RunRequest(run_id='six'))
 web.LOCK.acquire()
 try:
  with pytest.raises(RuntimeError): web.run_for_session(b,RunRequest(run_id='busy'))
 finally: web.LOCK.release()

def test_app():
 app=AppTest.from_file(str(ROOT/'streamlit_app.py'),default_timeout=40).run()
 assert not app.exception and not app.error
 for page in ['Pipeline 1','Pipeline 2','Pipeline 3','Agent Trace','Results & Feedback','Workflow Overview']:
  app.radio[0].set_value(page).run()
  assert not app.exception and all('SUBMISSION DISABLED' in e.value for e in app.error),page
 for scenario in SCENARIOS:
  next(s for s in app.selectbox if s.label=='Scenario Selector').select(scenario).run()
  assert not app.exception and not app.error,scenario
 next(b for b in app.button if b.label=='Start Demo').click().run()
 for i in range(1,6):
  next(b for b in app.button if b.label=='Next Step').click().run()
  assert app.session_state['story_step']==i
 assert not app.exception

def test_public_snapshot():
 for p in (ROOT/'PipeLine2/integration/fixtures/p1').glob('*.json'):
  text=p.read_text()
  assert '/Users/' not in text and '"full_text"' not in text and '"abstract"' not in text
