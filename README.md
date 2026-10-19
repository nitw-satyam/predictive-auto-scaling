# Predictive Auto Scaling — Integrated Mini-to-Major Project

This is one consolidated project containing the original mini-project implementation and the expanded major-project benchmark. The main implementation is the major-stage simulator; the earlier mini-stage code, results, diagrams, interactive demonstrator, and milestone reports are preserved in `mini_project_stage/` for traceability.

## Final deliverables
- `report/Predictive_Auto_Scaling_Integrated_Project_Report.docx` — single final editable report.
- `report/Predictive_Auto_Scaling_Integrated_Project_Report.pdf` — PDF export of the same report.
- `report/Predictive_Auto_Scaling_Integrated_Project_Presentation.pptx` — final presentation.
- `src/major_simulator.py` and `src/run_experiments.py` — primary benchmark implementation.
- `tests/` — automated tests.
- `results/` — raw traces, scenario metrics, overall metrics, configuration, and mini-vs-major comparison.
- `diagrams/` — architecture, workload, capacity, latency, SLA, scaling, forecasting, and cost/latency graphs.
- `interactive/interactive_autoscaling_demo.html` — browser-based interactive demonstrator.
- `mini_project_stage/` — preserved original mini-project code, results, diagrams, and milestone reports.

## Setup and reproduce
```bash
python -m pip install -r requirements.txt
python src/run_experiments.py
python -m pytest tests/
```
Run commands from the project root. The experiments use synthetic workloads and a simulated latency model. Results are not production cloud measurements or actual cloud billing data. The optional Flask/Locust/Docker files are starting points for later real load testing; cloud deployment is not claimed as completed.

## Project evolution
The mini stage introduced a reactive threshold baseline and a lightweight predictive controller using a trend-adjusted moving average, safety margin, hysteresis, and cooldown. The major stage expands the experiment to six workload classes, models instance warm-up and capacity bounds, adds richer latency/SLA/cost-proxy metrics, records scenario-level raw traces, and includes automated tests. The report retains the earlier results as a historical baseline and distinguishes them from the expanded benchmark.
