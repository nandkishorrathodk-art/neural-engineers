import json
import os
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import laya

base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "models", "laya", "base"))
model_path = base_dir if os.path.exists(base_dir) else r"C:\Users\nandk\.cache\huggingface\hub\models--convaiinnovations--laya\snapshots\55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"
print("Loading real Laya neural checkpoint from:", model_path)
t0 = time.perf_counter()
agent = laya.Agent(model_path)
t1 = time.perf_counter()
print(f"Cold-start model load time: {t1 - t0:.3f}s")

state = "The user wants to export analytics report data to an external document."
questions = {
    "format_choice": {
        "type": "choice",
        "instructions": "Which export format should be generated?",
        "criteria": {
            "csv": "Export as CSV spreadsheet table",
            "pdf": "Export as PDF printable document",
            "json": "Export as raw JSON API data"
        }
    },
    "severity_score": {
        "type": "score",
        "instructions": "What is the priority level of this request?",
        "criteria": ["low", "normal", "high", "critical"]
    },
    "requires_auth": {
        "type": "noul",
        "instructions": "Does this export require privileged authorization?"
    }
}

# Warm up run
print("\n--- Warm-up / First Inference ---")
t_start = time.perf_counter()
res = agent.predict(state, questions)
t_end = time.perf_counter()
warmup_ms = (t_end - t_start) * 1000.0
print(f"First inference latency: {warmup_ms:.2f}ms")

print(json.dumps(res, indent=2))

# Measure warm inference latencies over multiple runs
print("\n--- Warm Inference Latency Measurement (5 runs) ---")
latencies = []
for i in range(5):
    t_start = time.perf_counter()
    res = agent.predict(state, questions)
    t_end = time.perf_counter()
    lat = (t_end - t_start) * 1000.0
    latencies.append(lat)
    print(f"Run {i+1}: {lat:.2f}ms")

avg_lat = sum(latencies) / len(latencies)
print(f"Average warm inference latency: {avg_lat:.2f}ms")

