import pytest

@pytest.fixture(autouse=True)
def isolate_test_state_dir(tmp_path, monkeypatch):
    """Ensure each test runs with an isolated ORBIT_STATE_DIR so cache doesn't bleed between tests."""
    monkeypatch.setenv("ORBIT_STATE_DIR", str(tmp_path / "state"))
    from orbit.services.llm.gemini_client import get_process_rate_limiter
    get_process_rate_limiter().reset()

