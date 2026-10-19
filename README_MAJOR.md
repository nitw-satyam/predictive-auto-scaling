# Predictive Auto Scaling — Major Project Package

## Status
This is a simulation-backed major-project prototype. Current benchmark data is synthetic; no live cloud deployment or production measurements are claimed.

## Quick start
```bash
python -m pip install -r requirements.txt
python src/run_experiments.py
python -m pytest tests/
```
Run commands from the project root. Outputs are written to `results/`; plots are in `diagrams/`.

## Controller comparison
- Reactive: utilization thresholds, cooldown, scale-in after sustained low utilization.
- Predictive: trend-adjusted moving forecast, 15% safety margin, sustained overload/underload checks, cooldown, warm-up delay.
- Six generated workloads, fixed seed, 720 one-minute intervals per workload.

## Important interpretation
- `estimated_instance_minute_cost` is only a capacity-use proxy, not money.
- `response_ms` is generated from a simple model, not measured service latency.
- Previous mini-project results are preserved in `results/previous_vs_updated_summary.csv`. Because the simulator and workload suite changed, do not claim direct improvement from old to new values without rerunning both controllers under identical conditions.

## Major-project roadmap
See `report/Predictive_Auto_Scaling_Major_Project_Report.docx`, especially the roadmap and limitations sections. A final academic submission should add verified scholarly references, repeated-seed statistical analysis, real load testing, and (if feasible) cloud deployment.
