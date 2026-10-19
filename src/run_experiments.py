from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from major_simulator import run_benchmark
if __name__=='__main__':
    res,overall,workloads=run_benchmark(HERE.parent/'results')
    print(f'Ran {len(workloads)} scenarios x 2 controllers = {len(res)} scenario/controller runs')
    print(overall.to_string(index=False))
