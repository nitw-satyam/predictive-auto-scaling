import math
import numpy as np
import pandas as pd

CAPACITY_PER_INSTANCE = 220.0
MIN_INSTANCES = 1
MAX_INSTANCES = 8

def moving_forecast(history, window=12):
    if not history:
        return None
    h = history[-window:]
    if len(h) < 2:
        return h[-1]
    x = np.arange(len(h))
    slope, intercept = np.polyfit(x, h, 1)
    trend_prediction = slope * len(h) + intercept
    return max(0.0, 0.6*np.mean(h) + 0.4*trend_prediction)

def run_predictive(csv_path):
    data = pd.read_csv(csv_path)
    instances = 2
    cooldown = 5
    last_change = -999
    high_count = low_count = 0
    history = []
    records = []

    for i, row in data.iterrows():
        history.append(float(row["request_rate"]))
        prediction = moving_forecast(history[:-1], 12)
        if prediction is None:
            prediction = float(row["request_rate"])

        safety_margin = 1.20
        required = math.ceil(prediction*safety_margin/CAPACITY_PER_INSTANCE)
        required = min(MAX_INSTANCES, max(MIN_INSTANCES, required))

        current_load = row["request_rate"]/(instances*CAPACITY_PER_INSTANCE)
        overload = required > instances or current_load > 0.82
        underload = required < instances and current_load < 0.45

        high_count = high_count + 1 if overload else 0
        low_count = low_count + 1 if underload else 0
        action = "hold"

        if i - last_change >= cooldown:
            if high_count >= 3 and instances < MAX_INSTANCES:
                instances = min(MAX_INSTANCES, max(instances + 1, required))
                last_change = i
                high_count = low_count = 0
                action = "scale_out"
            elif low_count >= 6 and instances > MIN_INSTANCES:
                new_size = max(MIN_INSTANCES, required)
                if new_size < instances:
                    instances = new_size
                    last_change = i
                    high_count = low_count = 0
                    action = "scale_in"

        effective_capacity = instances*CAPACITY_PER_INSTANCE
        queue_ratio = max(0, row["request_rate"]/effective_capacity - 1)
        response_ms = 55 + 240*queue_ratio
        sla_violation = int(response_ms > 200)

        records.append({
            "time_min": row["time_min"],
            "request_rate": row["request_rate"],
            "predicted_request_rate": prediction,
            "instances": instances,
            "action": action,
            "response_ms": response_ms,
            "sla_violation": sla_violation
        })

    return pd.DataFrame(records)

if __name__ == "__main__":
    result = run_predictive("../data/workload.csv")
    result.to_csv("../results/predictive_output.csv", index=False)
    print(result.head(15).to_string(index=False))