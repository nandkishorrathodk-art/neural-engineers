from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.security.kill_switch import activate_kill_switch, reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def cleanup():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_decision_intelligence_api_endpoints(async_session: AsyncSession):
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health endpoint
        health_res = await client.get("/decision-intelligence/health")
        assert health_res.status_code == 200
        health_data = health_res.json()
        assert "health_state" in health_data

        # 2. CHOICE endpoint
        choice_payload = {
            "context": "Assign worker to write security unit tests",
            "candidates": [
                {"id": "emp_eng", "label": "Engineering Specialist"},
                {"id": "emp_sec", "label": "Security Specialist"},
                {"id": "emp_qa", "label": "QA Tester"},
            ],
            "domain": "routing",
        }
        choice_res = await client.post("/decision-intelligence/choice", json=choice_payload)
        assert choice_res.status_code == 200
        choice_data = choice_res.json()
        assert choice_data["selected_id"] in ["emp_sec", "emp_qa"]
        assert choice_data["confidence"] > 0.0
        assert choice_data["latency_ms"] < 60.0

        # 3. SCORE endpoint
        score_payload = {
            "proposition": "Memory allocation failure and segmentation fault",
            "context": "fatal critical crash",
            "scale_min": 1,
            "scale_max": 5,
            "domain": "urgency",
        }
        score_res = await client.post("/decision-intelligence/score", json=score_payload)
        assert score_res.status_code == 200
        score_data = score_res.json()
        assert score_data["score"] in [4, 5]

        # 4. NOUL endpoint
        noul_payload = {
            "proposition": "All test assertions passed with valid receipts",
            "context": "normal healthy stable",
            "threshold": 0.5,
            "domain": "blocker",
        }
        noul_res = await client.post("/decision-intelligence/noul", json=noul_payload)
        assert noul_res.status_code == 200
        noul_data = noul_res.json()
        assert noul_data["is_true"] is False

        # 5. Polymorphic Route endpoint
        route_payload = {
            "primitive": "CHOICE",
            "choice_question": {
                "context": "Pick best database engine",
                "candidates": [
                    {"id": "postgres", "label": "PostgreSQL"},
                    {"id": "sqlite", "label": "SQLite Embedded"},
                ],
            },
        }
        route_res = await client.post("/decision-intelligence/route", json=route_payload)
        assert route_res.status_code == 200
        route_data = route_res.json()
        assert route_data["primitive"] == "CHOICE"
        assert route_data["choice_result"] is not None

        # 6. Audit trail verification
        audit_res = await client.get("/decision-intelligence/audit")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["integrity_verified"] is True
        assert audit_data["entry_count"] >= 3

        # 7. Metrics endpoint
        metrics_res = await client.get("/decision-intelligence/metrics")
        assert metrics_res.status_code == 200
        metrics_data = metrics_res.json()
        assert "throughput" in metrics_data

        # 8. Benchmark runner endpoint
        bench_res = await client.post("/decision-intelligence/benchmark")
        assert bench_res.status_code == 200
        bench_data = bench_res.json()
        assert bench_data["benchmark_name"] == "ANCOS-DecisionBench"
        assert bench_data["passed_scenarios"] >= 4

        # 9. Promotion check endpoint
        promo_res = await client.post(
            "/decision-intelligence/promotion/check",
            params={"category": "gui_selection", "auto_promote": True},
        )
        assert promo_res.status_code == 200
        promo_data = promo_res.json()
        assert promo_data["category"] == "gui_selection"

        # 10. Rollback endpoint
        rollback_res = await client.post(
            "/decision-intelligence/rollback",
            params={"category": "gui_selection", "reason": "Drift detected"},
        )
        assert rollback_res.status_code == 200
        rollback_data = rollback_res.json()
        assert rollback_data["category"] == "gui_selection"
        assert rollback_data["reverted_to"] == "shadow_mode"

        # 11. Kill switch blocks API calls with 503
        activate_kill_switch("operator", "Emergency shutdown test")
        blocked_res = await client.post("/decision-intelligence/choice", json=choice_payload)
        assert blocked_res.status_code == 503
        assert "Kill Switch" in blocked_res.json()["detail"]
