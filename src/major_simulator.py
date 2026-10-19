"""Reproducible multi-controller auto-scaling benchmark.
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
