import sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import public_runtime
from agx_integration.contracts import RunRequest
from agx_integration.workflow import execute
from agx_integration_v1_1.runtime import open_bundle,SCENARIOS
for scenario in SCENARIOS:
    result=execute(RunRequest(run_id=f'public-v11-{scenario}',scenario=scenario,dft_budget_jobs=0),ROOT/'PipeLine2/runs/integration',pipeline3_root=ROOT/'PipeLine3')
    b=open_bundle(ROOT/'PipeLine2/runs/integration'/result['run_id'])
    print(scenario,result['status'],len(b.manifest['artifacts']))
