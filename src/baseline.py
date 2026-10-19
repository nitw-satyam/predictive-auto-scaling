import pandas as pd
import numpy as np

CAPACITY_PER_INSTANCE = 220.0
MIN_INSTANCES = 1
MAX_INSTANCES = 8

def run_baseline(csv_path):
    data = pd.read_csv(csv_path)
    instances = 2
    cooldown = 5
    last_change = -999
    records = []

    for i, row in data.iterrows():
        load = row["request_rate"]/(instances*CAPACITY_PER_INSTANCE)
        action = "hold"
        if i-last_change >= cooldown:
            if load > 0.70 and instances < MAX_INSTANCES:
                instances += 1
                last_change = i
                action = "scale_out"
            elif load < 0.30 and instances > MIN_INSTANCES:
                instances -= 1
                last_change = i
                action = "scale_in"

        capacity = instances*CAPACITY_PER_INSTANCE
        queue_ratio = max(0, row["request_rate"]/capacity - 1)
        response_ms = 55 + 240*queue_ratio

        records.append({
            "time_min": row["time_min"],
            "request_rate": row["request_rate"],
            "instances": instances,
            "action": action,
            "response_ms": response_ms,
            "sla_violation": int(response_ms > 200)
        })

    return pd.DataFrame(records)

if __name__ == "__main__":
    result = run_baseline("../data/workload.csv")
    result.to_csv("../results/baseline_output.csv", index=False)
    print(result.head(15).to_string(index=False))