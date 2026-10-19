from pathlib import Path
import numpy as np, pandas as pd, math, json, zipfile, shutil, textwrap
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from pptx import Presentation
from pptx.util import Inches as PInches, Pt as PPt

ROOT=Path('/mnt/data/major_autoscaling_project')
for d in ['src','data','results','diagrams','report','docs','tests','logs']: (ROOT/d).mkdir(parents=True,exist_ok=True)
# Preserve existing project files if present from zip, add new major-project layer.

sim_code=r'''"""Reproducible multi-controller auto-scaling benchmark.
All results are from a deterministic simulator, not a live cloud deployment."""
from pathlib import Path
import math
import numpy as np
import pandas as pd

CAPACITY_PER_INSTANCE = 220.0
MIN_INSTANCES, MAX_INSTANCES = 1, 12
WARMUP_MINUTES = 2
SLA_MS = 200.0

def make_workloads(seed=42, minutes=720):
    rng=np.random.default_rng(seed); t=np.arange(minutes)
    stable=np.clip(260 + 18*np.sin(t/18)+rng.normal(0,10,minutes),30,None)
    gradual=np.clip(80+0.95*t+rng.normal(0,22,minutes),25,None)
    spike=np.clip(180+22*np.sin(t/15)+rng.normal(0,12,minutes),25,None)
    spike[(t>=160)&(t<205)] += 1000
    spike[(t>=430)&(t<455)] += 700
    burst=np.clip(180+35*np.sin(t/5)+rng.normal(0,35,minutes),25,None)
    burst[(t%90>=15)&(t%90<30)] += 500
    noisy=np.clip(280+120*np.sin(t/9)+rng.normal(0,85,minutes),20,None)
    recovery=np.clip(100+0.3*t+rng.normal(0,18,minutes),20,None)
    recovery[(t>=250)&(t<330)] += 600
    recovery[t>=500] *= 0.35
    return {k:pd.DataFrame({'time_min':t,'request_rate':np.round(v,2)}) for k,v in {
      'stable':stable,'gradual_ramp':gradual,'sudden_spikes':spike,'repeated_bursts':burst,'noisy_fluctuation':noisy,'spike_and_recovery':recovery}.items()}

def forecast(history, window=12, horizon=2):
    if not history: return 0.0
    h=np.asarray(history[-window:],dtype=float)
    if len(h)<3: return float(h[-1])
    x=np.arange(len(h)); slope,intercept=np.polyfit(x,h,1)
    trend=max(0.0,intercept+slope*(len(h)-1+horizon))
    return max(0.0,0.55*float(np.mean(h[-min(5,len(h)):]))+0.45*trend)

def simulate(df, controller='reactive', capacity=CAPACITY_PER_INSTANCE, min_instances=MIN_INSTANCES, max_instances=MAX_INSTANCES,
             cooldown=3, warmup=WARMUP_MINUTES, safety=0.15, seed=42):
    rng=np.random.default_rng(seed)
    active=2; pending=[]; last_change=-999; high=low=0; hist=[]; rows=[]
    for i,row in df.reset_index(drop=True).iterrows():
        # Finish instance warm-up before processing the next control interval.
        ready=[p for p in pending if p<=i]
        active=min(max_instances,active+len(ready)); pending=[p for p in pending if p>i]
        demand=float(row.request_rate); hist.append(demand)
        predicted=forecast(hist[:-1]) if controller!='reactive' else np.nan
        effective=max(1,active)*capacity
        util=demand/effective
        action='hold'; desired=active
        can_change=(i-last_change)>=cooldown
        if controller=='reactive':
            if util>0.70: high+=1; low=0
            elif util<0.30: low+=1; high=0
            else: high=low=0
            if can_change and high>=1 and active+len(pending)<max_instances:
                pending.append(i+warmup); last_change=i; action='scale_out'; high=low=0
            elif can_change and low>=3 and active>min_instances:
                active-=1; last_change=i; action='scale_in'; high=low=0
        else:
            desired=int(np.clip(math.ceil(predicted*(1+safety)/capacity),min_instances,max_instances))
            forecast_trigger = desired > active + len(pending)
            overload = util > 0.88
            underload=(desired<active and util<0.48)
            high=high+1 if overload else 0; low=low+1 if underload else 0
            # Forecast-based capacity increases are proactive; utilization-only emergency scaling uses hysteresis.
            if can_change and (forecast_trigger or high>=2) and active+len(pending)<max_instances:
                add=max(1,desired-active-len(pending))
                add=min(add,max_instances-active-len(pending))
                if add>0: pending.extend([i+warmup]*add); last_change=i; action='scale_out'; high=low=0
            elif can_change and low>=5 and active>max(min_instances,desired):
                remove=min(active-max(min_instances,desired),active-min_instances)
                if remove>0: active-=remove; last_change=i; action='scale_in'; high=low=0
        # New instances do not serve requests until their warm-up ends.
        cap=max(1,active)*capacity
        queue_ratio=max(0.0,demand/cap-1.0)
        response=55+240*queue_ratio + rng.normal(0,3)
        response=max(20,response)
        rows.append({'time_min':int(row.time_min),'request_rate':demand,'predicted_request_rate':predicted,
          'active_instances':active,'pending_instances':len(pending),'total_instances':active+len(pending),
          'utilization':demand/cap,'action':action,'response_ms':response,
          'sla_violation':int(response>SLA_MS),'capacity_requests_per_min':cap})
    return pd.DataFrame(rows)

def metrics(df, cost_per_instance_min=1.0):
    acts=int((df.action!='hold').sum()); lat=df.response_ms
    return {'avg_active_instances':round(df.active_instances.mean(),3),'avg_provisioned_instances':round(df.total_instances.mean(),3),
      'peak_instances':int(df.total_instances.max()),'scale_actions':acts,'scale_out_actions':int((df.action=='scale_out').sum()),
      'scale_in_actions':int((df.action=='scale_in').sum()),'avg_response_ms':round(lat.mean(),2),
      'p95_response_ms':round(lat.quantile(.95),2),'p99_response_ms':round(lat.quantile(.99),2),
      'sla_violations':int(df.sla_violation.sum()),'sla_violation_pct':round(df.sla_violation.mean()*100,3),
      'estimated_instance_minute_cost':round(df.total_instances.sum()*cost_per_instance_min,2),
      'mean_absolute_forecast_error':round(float(np.nanmean(np.abs(df.predicted_request_rate-df.request_rate))),2) if df.predicted_request_rate.notna().any() else np.nan}

def run_benchmark(out_dir='../results', seed=42):
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    workloads=make_workloads(seed=seed); all_metrics=[]
    for wname,wdf in workloads.items():
        wdf.to_csv(out/f'workload_{wname}.csv',index=False)
        for controller in ['reactive','predictive']:
            run=simulate(wdf,controller=controller,seed=seed)
            run.insert(0,'scenario',wname); run.insert(1,'controller',controller)
            run.to_csv(out/f'{wname}_{controller}.csv',index=False)
            all_metrics.append({'scenario':wname,'controller':controller,**metrics(run)})
    result=pd.DataFrame(all_metrics); result.to_csv(out/'scenario_metrics.csv',index=False)
    overall=[]
    for controller in ['reactive','predictive']:
        frames=[pd.read_csv(out/f'{w}_{controller}.csv') for w in workloads]
        merged=pd.concat(frames,ignore_index=True)
        overall.append({'controller':controller,**metrics(merged)})
    pd.DataFrame(overall).to_csv(out/'overall_metrics.csv',index=False)
    return result,pd.DataFrame(overall),workloads

if __name__=='__main__':
    res,overall,_=run_benchmark()
    print('SCENARIO METRICS'); print(res.to_string(index=False)); print('\nOVERALL'); print(overall.to_string(index=False))
'''
(ROOT/'src/major_simulator.py').write_text(sim_code)

# Testable utility / experiment runner
(ROOT/'src/run_experiments.py').write_text(r'''from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from major_simulator import run_benchmark
if __name__=='__main__':
    res,overall,workloads=run_benchmark(HERE.parent/'results')
    print(f'Ran {len(workloads)} scenarios x 2 controllers = {len(res)} scenario/controller runs')
    print(overall.to_string(index=False))
''')
(ROOT/'tests/test_major_simulator.py').write_text(r'''import sys
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
''')

# run benchmark
import sys
sys.path.insert(0,str(ROOT/'src'))
from major_simulator import run_benchmark, make_workloads, simulate, metrics
scen,overall,workloads=run_benchmark(ROOT/'results')

# Graphs: workload and traces by scenario
plt.rcParams.update({'figure.figsize':(10,4.8),'axes.grid':True,'font.size':9})
for name,wdf in workloads.items():
    fig,ax=plt.subplots(); ax.plot(wdf.time_min,wdf.request_rate,linewidth=1.4); ax.set(title=f'Generated workload: {name.replace("_"," ").title()}',xlabel='Time (minute)',ylabel='Requests per minute'); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/f'workload_{name}.png',dpi=160); plt.close(fig)
# comparisons by scenario
for metric,title,ylabel,filename in [('avg_provisioned_instances','Average provisioned instances by workload','Instances','avg_instances_by_scenario.png'),('p95_response_ms','P95 response latency by workload','Milliseconds','p95_latency_by_scenario.png'),('sla_violation_pct','SLA violation rate by workload','Violations (%)','sla_by_scenario.png'),('scale_actions','Scaling actions by workload','Actions','scaling_actions_by_scenario.png'),('estimated_instance_minute_cost','Estimated capacity cost by workload','Instance-minutes','cost_by_scenario.png')]:
    piv=scen.pivot(index='scenario',columns='controller',values=metric)
    ax=piv.plot(kind='bar',rot=20); ax.set_title(title); ax.set_ylabel(ylabel); ax.set_xlabel('Workload scenario'); ax.legend(['Reactive baseline','Predictive controller']); fig=ax.get_figure(); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/filename,dpi=160); plt.close(fig)
# timeline comparison for spike workload
for col,title,ylabel,filename in [('request_rate','Demand trace under sudden spikes','Requests/min','demand_spikes.png'),('active_instances','Instance count: reactive vs predictive','Active instances','instance_timeline_spikes.png'),('response_ms','Response latency: reactive vs predictive','Response time (ms)','latency_timeline_spikes.png')]:
    fig,ax=plt.subplots();
    for c,label in [('reactive','Reactive baseline'),('predictive','Predictive controller')]:
        df=pd.read_csv(ROOT/'results'/f'sudden_spikes_{c}.csv'); ax.plot(df.time_min,df[col],label=label,linewidth=1.3)
    ax.set(title=title,xlabel='Time (minute)',ylabel=ylabel); ax.legend(); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/filename,dpi=160); plt.close(fig)
# prediction error plot
fig,ax=plt.subplots(); df=pd.read_csv(ROOT/'results'/'sudden_spikes_predictive.csv'); ax.plot(df.time_min,df.request_rate,label='Observed demand'); ax.plot(df.time_min,df.predicted_request_rate,label='Forecast demand',alpha=.85); ax.set(title='Forecast vs observed demand: sudden-spike scenario',xlabel='Time (minute)',ylabel='Requests/min'); ax.legend(); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/'forecast_vs_actual.png',dpi=160); plt.close(fig)
# cost-latency tradeoff
fig,ax=plt.subplots(); ax.scatter(scen[scen.controller=='reactive'].estimated_instance_minute_cost,scen[scen.controller=='reactive'].p95_response_ms,label='Reactive',s=55); ax.scatter(scen[scen.controller=='predictive'].estimated_instance_minute_cost,scen[scen.controller=='predictive'].p95_response_ms,label='Predictive',s=55); 
for _,r in scen.iterrows(): ax.annotate(r.scenario.replace('_',' '),(r.estimated_instance_minute_cost,r.p95_response_ms),fontsize=6,xytext=(3,3),textcoords='offset points')
ax.set(title='Cost–latency trade-off across scenarios',xlabel='Estimated instance-minutes (proxy cost)',ylabel='P95 response time (ms)'); ax.legend(); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/'cost_latency_tradeoff.png',dpi=160); plt.close(fig)

# Comparative results README and documentation
old_summary=pd.DataFrame([
 {'version':'Earlier mini-project simulation','controller':'Reactive baseline','average_instances':1.933,'peak_instances':4,'scale_actions':21,'SLA_violations':0,'estimated_cost_units':6.96},
 {'version':'Earlier mini-project simulation','controller':'Predictive + hysteresis','average_instances':1.794,'peak_instances':4,'scale_actions':17,'SLA_violations':0,'estimated_cost_units':6.46},
])
old_summary.to_csv(ROOT/'results'/'previous_vs_updated_summary.csv',index=False)
# comparison graph previous outcomes and new benchmark overall are separate protocols
fig,axs=plt.subplots(1,2,figsize=(10,4.4))
axs[0].bar(['Reactive','Predictive'],[1.933,1.794]); axs[0].set_title('Earlier mini-project: average instances'); axs[0].set_ylabel('Average instances')
axs[1].bar(['Reactive','Predictive'],[21,17]); axs[1].set_title('Earlier mini-project: scaling actions'); axs[1].set_ylabel('Action count')
fig.suptitle('Previously reported mini-project results (original 360-minute trace)'); fig.tight_layout(); fig.savefig(ROOT/'diagrams'/'previous_results.png',dpi=160); plt.close(fig)

# Major report generation
report=Document(); sec=report.sections[0]; sec.top_margin=Inches(.65); sec.bottom_margin=Inches(.65); sec.left_margin=Inches(.75); sec.right_margin=Inches(.75)
styles=report.styles; styles['Normal'].font.name='Arial'; styles['Normal'].font.size=Pt(9.5)
for sty in ['Heading 1','Heading 2','Heading 3']: styles[sty].font.name='Arial'
p=report.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=p.add_run('MAJOR PROJECT REPORT'); r.bold=True; r.font.size=Pt(18)
p=report.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER
r=p.add_run('Predictive Auto Scaling Using Workload Forecasting, Hysteresis and Safety-Aware Capacity Planning'); r.bold=True; r.font.size=Pt(15)
p=report.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.add_run('Department of Computer Science and Engineering\nNational Institute of Technology, Warangal\nAcademic Year: 2026-27').font.size=Pt(11)
t=report.add_table(rows=0,cols=2); t.style='Light Shading Accent 1'; t.alignment=WD_TABLE_ALIGNMENT.CENTER
for a,b in [('Field','Details'),('Student Name / Roll No.','Dharmendra Kumar Gupta — 26CSM1R35\nSatyam Kumar Azad — 26CSM1R22'),('Mentor','Dr. Manoj Kumar Patra'),('Project Type','Major Project — simulation-backed research prototype'),('Implementation Status','Local simulation and reproducible benchmark; live cloud deployment is future work')]:
    cells=t.add_row().cells; cells[0].text=a; cells[1].text=b
report.add_page_break()

def heading(s,l=1): report.add_heading(s,level=l)
def para(s): report.add_paragraph(s)
def bullets(items):
    for x in items: report.add_paragraph(x,style='List Bullet')
def table(headers, rows):
    tb=report.add_table(rows=1,cols=len(headers)); tb.style='Light Shading Accent 1'
    for c,h in zip(tb.rows[0].cells,headers): c.text=str(h)
    for row in rows:
        cells=tb.add_row().cells
        for c,v in zip(cells,row): c.text=str(v)
    return tb
def img(name,caption,width=6.2):
    path=ROOT/'diagrams'/name
    if path.exists():
        p=report.add_paragraph(); p.alignment=WD_ALIGN_PARAGRAPH.CENTER; p.add_run().add_picture(str(path),width=Inches(width))
        cp=report.add_paragraph(caption); cp.alignment=WD_ALIGN_PARAGRAPH.CENTER

heading('Abstract'); para('Cloud applications experience demand changes that can cause excess provisioning, slow response, and repeated scaling actions. This project upgrades an initial mini-project into a reproducible benchmark study of a reactive threshold autoscaler and a predictive controller. The predictive controller forecasts short-term request demand using a trend-adjusted moving average, adds a safety margin, estimates required capacity, and combines the decision with hysteresis, cooldowns, and instance warm-up delay. The extended experimental suite includes six synthetic workload profiles, two controller strategies, request/instance/latency traces, scenario-level metrics, and automated tests. The simulator measures provisioning, scale actions, average and tail latency, SLA violations, forecast error, and estimated instance-minute cost. Results are explicitly labeled as simulated and are not represented as real cloud measurements. The project package includes runnable source code, generated datasets, CSV outputs, plots, report, test suite, and deployment roadmap.')
heading('1. Introduction and Problem Statement'); heading('1.1 Background',2); para('Auto scaling changes the number of compute instances in response to application demand. Reactive systems react after load crosses a threshold, while predictive systems attempt to provision capacity before the full demand arrives. A useful design must balance latency, capacity cost, stability, and the delay required to bring new instances into service.')
heading('1.2 Problem statement',2); para('A single CPU threshold can react late to abrupt spikes and can oscillate when demand fluctuates around a boundary. Scaling decisions that ignore forecast uncertainty, warm-up time, and service-level objectives can produce poor trade-offs. The project evaluates whether short-horizon demand prediction plus conservative decision controls improves the resource/latency trade-off under varied workloads.')
heading('1.3 Objectives',2); bullets(['Implement a deterministic, reproducible simulation of reactive and predictive scaling.','Generate six workload profiles covering stable, ramp, spike, burst, noisy, and recovery behaviour.','Model capacity limits, instance warm-up delay, cooldown, hysteresis, and a simple latency/SLA proxy.','Benchmark cost proxy, provisioning, scale actions, mean/p95/p99 latency, SLA violations, and forecast error.','Produce graph-rich outputs and automated tests so experiments can be repeated.','Define a staged route toward real cloud validation without claiming that it has already been deployed.'])
heading('2. Existing Mini Project Review'); para('The earlier mini-project used one 360-minute synthetic trace and reported a reactive baseline against a predictive controller. The previously reported values are preserved below for historical comparison. The major-project experiment is broader and uses a different workload suite and simulator implementation, so its results should not be treated as a strict apples-to-apples rerun of those earlier numbers.')
table(['Earlier mini-project metric','Reactive baseline','Predictive + hysteresis'],[['Average instances','1.933','1.794'],['Peak instances','4','4'],['Scaling actions','21','17'],['SLA violations','0','0'],['Estimated cost units','6.96','6.46']])
img('previous_results.png','Figure 1. Previously reported mini-project outcomes; retained as historical baseline.')
heading('3. Literature and Design Rationale'); para('The design follows common autoscaling themes: threshold-based reactive control, workload forecasting, hysteresis to reduce oscillation, cooldowns to avoid repeated changes, and warm-up-aware capacity planning. This project is an engineering evaluation rather than a systematic literature review; final academic submission should replace or supplement this section with verified papers selected by the team and cited in the required institutional style.')
bullets(['Reactive threshold control: simple and explainable, but reacts after a threshold crossing.','Predictive control: estimates near-future demand, potentially starting provisioning earlier; quality depends on workload predictability.','Hysteresis/cooldown: avoids rapid scale-in/scale-out reversals but can delay response if too conservative.','Warm-up modelling: makes newly requested capacity unavailable until its startup delay expires.','Multi-scenario evaluation: avoids relying on one trace and exposes controller strengths and weaknesses under different patterns.'])
heading('4. Proposed Architecture and Methodology'); img('implementation_architecture.png','Figure 2. System implementation architecture carried forward from the mini-project.')
para('The simulator accepts a time-indexed request-rate trace. At each interval it updates pending instances, estimates current utilization, optionally forecasts future demand, chooses a scaling action, applies warm-up and cooldown rules, and records the resulting capacity and response-time proxy. The same workload seed is used for both controllers to support a controlled comparison.')
heading('4.1 Controller logic',2); bullets(['Reactive baseline: scale out when measured utilization exceeds 70%; scale in after sustained low utilization below 30%; obey cooldown and instance bounds.','Predictive controller: forecast near-term request rate from recent observations; calculate ceil(forecast × (1 + safety margin) / per-instance capacity); clamp desired capacity to minimum and maximum; require sustained overload before scaling out and sustained underload before scaling in.','Warm-up: newly requested instances are pending for two simulation intervals before joining active capacity.','Capacity limits: minimum one and maximum twelve instances in the major benchmark.','Latency proxy: response time is computed from a simple queue-over-capacity model plus small deterministic noise; SLA threshold is 200 ms.'])
heading('4.2 Key equations',2); para('Required instances = ceil(predicted request rate × (1 + safety margin) / capacity per instance). In the benchmark, capacity per instance is 220 requests/minute and the safety margin is 15%. The response-time equation is a simulation proxy, not a calibrated performance model: latency = 55 ms + 240 × max(0, demand / active capacity − 1) + noise.')
heading('4.3 Architecture / decision flow',2); img('proposed_solution_architecture_methodology.png','Figure 3. Proposed controller and decision methodology.')
heading('5. Experimental Setup and Benchmarking'); para('Environment: Python 3, NumPy, Pandas, Matplotlib; CSV traces and fixed random seeds; no cloud billing or real service telemetry is involved. Each of six workload profiles runs through both controllers for 720 one-minute intervals. Per-instance capacity is 220 requests/minute, capacity is bounded to 1–12 instances, warm-up is two minutes, predictive safety margin is 15%, and the SLA proxy threshold is 200 ms.')
table(['Scenario','Purpose'],[['Stable','Check whether controller avoids unnecessary capacity changes.'],['Gradual ramp','Assess proactive capacity as demand rises.'],['Sudden spikes','Expose reaction delay and warm-up effects.'],['Repeated bursts','Test repeated short bursts and cooldown behaviour.'],['Noisy fluctuation','Test sensitivity to irregular demand.'],['Spike and recovery','Evaluate scaling out and later scaling in.']])
img('experimental_design_flow.png','Figure 4. Experimental design and comparison pipeline.')
heading('5.1 Metrics',2); bullets(['Average active/provisioned instances and peak provisioned instances.','Number of scale-out and scale-in actions.','Average, p95, and p99 response-time proxy.','SLA violation count and percentage of intervals above 200 ms.','Mean absolute forecast error for predictive controller.','Estimated instance-minutes as a transparent cost proxy; this is not a currency estimate.'])
heading('6. Updated Results and Analysis'); para('The following results are generated by the new major-project simulator. They are simulated benchmark outcomes and should be rerun on the target machine before submission. The CSV files in results/ contain scenario-level raw records and aggregated metrics.')
# overall and scenario tables
rows=[]
for _,r in overall.iterrows(): rows.append([r['controller'],r['avg_provisioned_instances'],r['peak_instances'],r['scale_actions'],r['avg_response_ms'],r['p95_response_ms'],r['sla_violation_pct'],r['estimated_instance_minute_cost'],r['mean_absolute_forecast_error']])
table(['Controller','Avg prov.','Peak','Actions','Avg ms','P95 ms','SLA %','Instance-min','MAE req/min'],rows)
for fn,cap in [('avg_instances_by_scenario.png','Figure 5. Average provisioned capacity by scenario.'),('p95_latency_by_scenario.png','Figure 6. Tail latency comparison by scenario.'),('sla_by_scenario.png','Figure 7. SLA violation rate by scenario.'),('scaling_actions_by_scenario.png','Figure 8. Scaling action count by scenario.'),('cost_by_scenario.png','Figure 9. Estimated capacity-cost proxy by scenario.'),('demand_spikes.png','Figure 10. Sudden-spike demand trace.'),('instance_timeline_spikes.png','Figure 11. Active instance timeline during sudden spikes.'),('latency_timeline_spikes.png','Figure 12. Response-time timeline during sudden spikes.'),('forecast_vs_actual.png','Figure 13. Predictive forecast against observed demand.'),('cost_latency_tradeoff.png','Figure 14. Cost–latency trade-off across workload scenarios.')]: img(fn,cap)
heading('6.1 How to interpret the results',2); para('Do not select a controller from one metric alone. Fewer instances or lower instance-minutes may indicate lower cost, but must be considered alongside tail latency and SLA violations. Fewer scaling actions may indicate stability, but can also indicate a controller that reacts too slowly. The scenario-level tables should be inspected to identify where the predictive strategy helps and where forecasting error or warm-up delay remains costly.')
heading('6.2 Previous versus major-project results',2); para('The earlier project’s two-controller summary is retained in results/previous_vs_updated_summary.csv and Figure 1. The new suite expands from one trace to six generated workload classes and adds warm-up delay, p95/p99 latency, forecast error, per-scenario breakdowns, and test coverage. Because the workload and model have changed, do not directly subtract the old summary from the new overall metrics or present the difference as a controlled improvement. A future fair comparison should run both old and new controllers against identical traces, configuration, and simulator semantics.')
heading('7. Cases Addressed'); bullets(['Stable workload: avoid unnecessary churn.','Gradual increase: estimate near-term capacity before load continues to rise.','Sudden spike: measure impact of reaction delay and warm-up.','Repeated bursts: examine cooldown and oscillation behaviour.','Noisy workload: examine sensitivity to short-term variation.','Recovery/low demand: evaluate scale-in and capacity reduction.','Capacity limit: cap at twelve instances and observe residual overload.','Forecast error: compare predicted and observed request rates.'])
heading('8. Scope and Limitations'); bullets(['Workloads are synthetic and deterministic for reproducibility; they are not traces from a production service.','Response time is a simplified queue-over-capacity proxy, not a full web server/load-balancer benchmark.','Instance-minute cost is a relative proxy, not actual cloud billing.','Only request rate is used for forecasting; CPU, memory, queue length, and error rates are not fused into the controller.','Forecast uncertainty is not explicitly modelled as a confidence interval.','Warm-up is fixed at two minutes and does not represent real VM startup distributions.','The simulator is single-service and does not model database bottlenecks, network contention, containers, or multi-tier dependencies.','The included Flask/Locust and Docker files are starting points; a cloud deployment and load-test validation are not claimed as completed.'])
heading('9. Testing and Reproducibility'); bullets(['Run: python src/run_experiments.py from the project root.','Install dependencies: pip install -r requirements.txt.','Run tests: python -m pytest tests/ (pytest may need to be installed separately).','Outputs are written to results/; graphs are saved in diagrams/.','Use fixed seeds to reproduce the same generated workloads.','Keep raw CSVs and configuration with every report revision.'])
heading('10. Implementation Roadmap to a Full Major Project'); table(['Phase','Deliverable','Acceptance evidence'],[['1. Reproducible simulation','Current package: six workload profiles, two controllers, CSV results and graphs','Successful script run and tests'],['2. Stronger forecasting','Compare moving average with Linear Regression, Random Forest/XGBoost, and seasonal naive baseline','MAE/RMSE by scenario; train/test split with no leakage'],['3. Multi-metric control','Include CPU, memory, queue depth and request latency','Ablation study showing contribution of each metric'],['4. Statistical evaluation','Run 20+ seeds or bootstrap confidence intervals','Mean, standard deviation, confidence intervals'],['5. Real application load test','Flask service + Locust; collect actual throughput and latency','Raw load-test results and repeatable scripts'],['6. Cloud prototype','Deploy to a selected cloud autoscaling group or Kubernetes HPA/KEDA','Architecture, configuration, measured startup and cost'],['7. Safety and observability','Add action logs, rollback guardrails, min/max policy and dashboard','Audit log, failure tests, operational runbook'],['8. Final validation','Same workloads/configuration for all baselines; independent test traces','Reproducible tables, limitations, supervisor review']])
heading('11. Future Scope'); bullets(['Compare statistical and ML predictors (seasonal naive, linear regression, Random Forest, XGBoost, LSTM/GRU).','Use prediction intervals or confidence-aware safety margins.','Add multi-metric signals such as CPU, memory, queue depth, request errors, and latency.','Calibrate the simulator with real load tests and empirical startup distributions.','Evaluate on real workload traces and run repeated trials with confidence intervals.','Deploy to a cloud managed instance group or Kubernetes HPA/KEDA; compare with provider-native autoscaling.','Add a dashboard, audit logs, anomaly detection, policy guardrails, and cost/carbon-aware objectives.'])
heading('12. Conclusion'); para('This major-project version turns the earlier demonstration into a reproducible comparative study with multiple workload classes, warm-up-aware simulation, richer metrics, raw result files, and a graph set suitable for review. It provides a stronger research and engineering foundation, but it is not yet a production cloud deployment. The next essential steps are stronger baseline comparisons, repeated-seed statistical evaluation, real application load testing, and eventual cloud validation.')
heading('Appendix A: Project file map'); bullets(['src/major_simulator.py — workload generation, controllers, simulation, metrics.','src/run_experiments.py — benchmark entry point.','tests/test_major_simulator.py — basic automated checks.','results/scenario_metrics.csv and overall_metrics.csv — aggregate results.','results/*_reactive.csv and results/*_predictive.csv — raw time series.','diagrams/*.png — graphs and architecture diagrams.','interactive/interactive_autoscaling_demo.html — original interactive demonstrator.'])
heading('References and academic note'); para('The current package preserves the project’s engineering rationale but does not fabricate bibliographic entries. For a final academic submission, add verified research papers on reactive/predictive autoscaling, workload forecasting, hysteresis, cloud benchmarking, and the selected cloud platform. Record exact author/title/venue/year/DOI and cite them in the body using the institution’s required style.')
report_path=ROOT/'report'/'Predictive_Auto_Scaling_Major_Project_Report.docx'; report.save(report_path)

# PPTX summary deck
prs=Presentation(); prs.slide_width=PInches(13.333); prs.slide_height=PInches(7.5)
def slide(title,body=None,image=None):
    s=prs.slides.add_slide(prs.slide_layouts[5]); s.shapes.title.text=title
    if body:
        tb=s.shapes.add_textbox(PInches(.7),PInches(1.35),PInches(11.9),PInches(5.4)); tf=tb.text_frame; tf.word_wrap=True
        for i,line in enumerate(body):
            p=tf.paragraphs[0] if i==0 else tf.add_paragraph(); p.text=line; p.font.size=PPt(21); p.space_after=PPt(10)
    if image:
        s.shapes.add_picture(str(ROOT/'diagrams'/image),PInches(.8),PInches(1.25),width=PInches(11.7),height=PInches(5.7))
    return s
slide('Predictive Auto Scaling','Major Project | Computer Science and Engineering\nDharmendra Kumar Gupta (26CSM1R35)\nSatyam Kumar Azad (26CSM1R22)\nMentor: Dr. Manoj Kumar Patra\nAcademic Year: 2026-27'.split('\n'))
slide('Problem and objectives',['Reactive thresholds can react late to spikes.','Frequent decisions can cause oscillation and unnecessary cost.','Compare reactive control with forecast + safety margin + hysteresis.','Evaluate six workload patterns using latency, SLA, capacity, actions and cost proxy.'])
slide('System architecture',image='implementation_architecture.png')
slide('Methodology',['Generate fixed-seed workloads.','Run both controllers on identical scenario traces.','Model capacity per instance, warm-up delay, cooldown and SLA proxy.','Export raw CSVs, aggregate metrics, graphs and tests.'])
slide('Benchmark scenarios',['Stable demand','Gradual ramp','Sudden spikes','Repeated bursts','Noisy fluctuation','Spike and recovery'])
slide('Average capacity comparison',image='avg_instances_by_scenario.png')
slide('Tail latency comparison',image='p95_latency_by_scenario.png')
slide('SLA comparison',image='sla_by_scenario.png')
slide('Scaling actions comparison',image='scaling_actions_by_scenario.png')
slide('Demand and scaling timeline',image='instance_timeline_spikes.png')
slide('Forecast quality',image='forecast_vs_actual.png')
slide('Cost–latency trade-off',image='cost_latency_tradeoff.png')
slide('Limitations and honest claims',['All current benchmark values are simulated.','Synthetic workloads and simplified latency proxy.','Instance-minute count is a relative cost proxy, not billing.','Cloud deployment and real load testing remain future phases.'])
slide('Roadmap to final major project',['Compare additional forecasting models.','Run repeated seeds and report confidence intervals.','Integrate CPU/memory/queue metrics.','Run Locust against a real service.','Deploy to a cloud autoscaler or Kubernetes and measure actual outcomes.'])
slide('Conclusion',['The package now has a broader benchmark, raw traces, tests and graph-rich reporting.','The next quality milestone is same-trace baseline evaluation and real workload validation.'])
ppt_path=ROOT/'report'/'Predictive_Auto_Scaling_Major_Project_Presentation.pptx'; prs.save(ppt_path)

# README / requirements
(ROOT/'README_MAJOR.md').write_text('''# Predictive Auto Scaling — Major Project Package\n\n## Status\nThis is a simulation-backed major-project prototype. Current benchmark data is synthetic; no live cloud deployment or production measurements are claimed.\n\n## Quick start\n```bash\npython -m pip install -r requirements.txt\npython src/run_experiments.py\npython -m pytest tests/\n```\nRun commands from the project root. Outputs are written to `results/`; plots are in `diagrams/`.\n\n## Controller comparison\n- Reactive: utilization thresholds, cooldown, scale-in after sustained low utilization.\n- Predictive: trend-adjusted moving forecast, 15% safety margin, sustained overload/underload checks, cooldown, warm-up delay.\n- Six generated workloads, fixed seed, 720 one-minute intervals per workload.\n\n## Important interpretation\n- `estimated_instance_minute_cost` is only a capacity-use proxy, not money.\n- `response_ms` is generated from a simple model, not measured service latency.\n- Previous mini-project results are preserved in `results/previous_vs_updated_summary.csv`. Because the simulator and workload suite changed, do not claim direct improvement from old to new values without rerunning both controllers under identical conditions.\n\n## Major-project roadmap\nSee `report/Predictive_Auto_Scaling_Major_Project_Report.docx`, especially the roadmap and limitations sections. A final academic submission should add verified scholarly references, repeated-seed statistical analysis, real load testing, and (if feasible) cloud deployment.\n''')
# update requirements
(ROOT/'requirements.txt').write_text('numpy\npandas\nmatplotlib\nscikit-learn\nflask\nlocust\npython-docx\npython-pptx\npytest\n')
# CSV historical old summary and new aggregate are included. Write machine-readable config
(ROOT/'results'/'benchmark_config.json').write_text(json.dumps({'seed':42,'minutes_per_scenario':720,'scenarios':list(workloads),'controllers':['reactive','predictive'],'capacity_per_instance_req_per_min':220,'min_instances':1,'max_instances':12,'warmup_minutes':2,'cooldown_minutes':3,'predictive_safety_margin':0.15,'sla_threshold_ms':200,'note':'Synthetic simulation; not live cloud measurements.'},indent=2))

# Try PDF conversion if libreoffice is installed; otherwise keep DOCX deliverable.
import subprocess
lo=shutil.which('libreoffice') or shutil.which('soffice')
if lo:
    subprocess.run([lo,'--headless','--convert-to','pdf','--outdir',str(ROOT/'report'),str(report_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)

# bundle complete directory excluding the builder to keep it understandable but include builder for reproducibility
zip_path=Path('/mnt/data/Predictive_Auto_Scaling_Major_Project.zip')
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
    for p in ROOT.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            z.write(p,Path('Predictive_Auto_Scaling_Major_Project')/p.relative_to(ROOT))
print('REPORT',report_path)
print('PRESENTATION',ppt_path)
print('ZIP',zip_path)
print('\nSCENARIO METRICS\n',scen.to_string(index=False))
print('\nOVERALL\n',overall.to_string(index=False))
