import json
from collections import Counter

with open("frontend/audit/audit_log.json", "r", encoding="utf-8") as f:
    logs = json.load(f)

print(f"Total log entries: {len(logs)}")
types = Counter(l.get("type") for l in logs)
print("Event type distribution:")
for k, v in types.most_common():
    print(f"  {k}: {v}")

errors = [
    l for l in logs
    if l.get("type") in ("pageerror", "http_error") or
       (l.get("type") == "console" and l.get("level") == "error")
]
print(f"\nTotal errors: {len(errors)}")

error_groups = Counter()
for e in errors:
    txt = e.get("text") or e.get("message") or f"{e.get('status')} {e.get('url')}"
    # Normalize URLs / IDs
    if "blocked by CORS policy" in txt:
        error_groups["CORS policy violation on localhost:4173"] += 1
    elif "Failed to load resource" in txt:
        error_groups["Failed to load resource (CORS / network)"] += 1
    elif "404" in txt:
        error_groups["HTTP 404 response"] += 1
    else:
        error_groups[txt[:120]] += 1

print("\nGrouped errors:")
for k, v in error_groups.most_common():
    print(f"  [{v}x] {k}")

# Check interactions
interactions = [l for l in logs if l.get("type") == "interaction"]
print(f"\nTotal interactions captured: {len(interactions)}")
failed_sanity = [i for i in interactions if not i.get("sanity", {}).get("ok")]
print(f"Interactions failing sanity check: {len(failed_sanity)}")
for fs in failed_sanity:
    print(f"  - {fs.get('label')}: {fs.get('sanity')}")
