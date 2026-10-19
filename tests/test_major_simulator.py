import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from major_simulator import make_workloads, simulate, metrics

def test_workloads_have_expected_scenarios():
    w=make_workloads(seed=1,minutes=120)
    assert len(w)==6 and all(len(x)==120 for x in w.values())

def test_controller_outputs_are_bounded():
    w=make_workloads(seed=2,minutes=120)['sudden_spikes']
    for c in ['reactive','predictive']:
        r=simulate(w,controller=c,seed=3)
        assert r.active_instances.min()>=1
        assert r.total_instances.max()<=12
        assert {'response_ms','sla_violation','action'} <= set(r.columns)
        assert metrics(r)['scale_actions']>=0
