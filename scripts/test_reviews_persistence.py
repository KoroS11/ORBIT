import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from orbit.api import create_app

def test_reviews_persistence():
    temp_dir = tempfile.mkdtemp(prefix="orbit_review_test_")
    os.environ["ORBIT_STATE_DIR"] = temp_dir
    os.environ["ORBIT_LLM"] = "stub"
    try:
        # 1. First run of backend
        app1 = create_app()
        with TestClient(app1) as client1:
            # POST approve
            r1 = client1.post("/decisions/test-dec-1/review", json={
                "status": "approved",
                "actor": "operator-alpha"
            })
            assert r1.status_code == 200, f"Approve failed: {r1.text}"
            data1 = r1.json()
            assert data1["status"] == "approved"
            assert data1["decision_id"] == "test-dec-1"

            # POST reject
            r2 = client1.post("/decisions/test-dec-2/review", json={
                "status": "rejected",
                "actor": "commander-bravo"
            })
            assert r2.status_code == 200, f"Reject failed: {r2.text}"
            data2 = r2.json()
            assert data2["status"] == "rejected"
            assert data2["decision_id"] == "test-dec-2"

            # GET /decisions/reviews
            res_before = client1.get("/decisions/reviews")
            assert res_before.status_code == 200
            reviews_before = res_before.json()["reviews"]
            assert len(reviews_before) == 2

        # Verify physical file exists on disk in ORBIT_STATE_DIR
        state_file = Path(temp_dir) / "decision_reviews.json"
        assert state_file.exists(), "decision_reviews.json was not written to ORBIT_STATE_DIR"
        disk_content = json.loads(state_file.read_text(encoding="utf-8"))
        assert "test-dec-1" in disk_content
        assert "test-dec-2" in disk_content
        assert disk_content["test-dec-1"]["status"] == "approved"
        assert disk_content["test-dec-2"]["status"] == "rejected"

        # 2. Simulate Backend Restart (new app instance in new lifespan context)
        app2 = create_app()
        with TestClient(app2) as client2:
            res_after = client2.get("/decisions/reviews")
            assert res_after.status_code == 200
            reviews_after = res_after.json()["reviews"]
            assert len(reviews_after) == 2, f"Expected 2 reviews after restart, got {len(reviews_after)}"

            assert reviews_after["test-dec-1"]["status"] == "approved"
            assert reviews_after["test-dec-1"]["actor"] == "operator-alpha"

            assert reviews_after["test-dec-2"]["status"] == "rejected"
            assert reviews_after["test-dec-2"]["actor"] == "commander-bravo"

        print("PERSISTENCE CHECK OK: Reviews persist across backend restart via ORBIT_STATE_DIR.")
        print(f"Verified {len(reviews_after)} reviews loaded from disk after restart:")
        for dec_id, r in reviews_after.items():
            print(f"  [{dec_id}] -> status: {r['status']}, actor: {r['actor']}, timestamp: {r.get('timestamp')}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    test_reviews_persistence()
