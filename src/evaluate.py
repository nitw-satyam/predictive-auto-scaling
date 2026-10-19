import pandas as pd

def summarize(path):
    df = pd.read_csv(path)
    return {
        "average_instances": round(df["instances"].mean(), 3),
        "peak_instances": int(df["instances"].max()),
        "scale_actions": int((df["action"] != "hold").sum()),
        "sla_violations": int(df["sla_violation"].sum()),
        "sla_rate_pct": round(df["sla_violation"].mean()*100, 3)
    }

if __name__ == "__main__":
    for name, path in [("Baseline","../results/baseline_output.csv"),
                       ("Proposed","../results/predictive_output.csv")]:
        print(name, summarize(path))