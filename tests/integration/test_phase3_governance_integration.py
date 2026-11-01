import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_governance_api_full_boardroom_deliberation_and_execution(async_session: AsyncSession):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Fetch Committee Members
        committee_res = await client.get("/governance/committee")
        assert committee_res.status_code == 200
        members = committee_res.json()
        assert len(members) >= 5
        member_ids = [m["member_id"] for m in members]
        assert "BossAI" in member_ids

        # 2. Create Strategic Proposal
        prop_res = await client.post(
            "/governance/proposals",
            json={
                "title": "Autonomous Model Routing Gateway V2",
                "description": "Implement circuit breakers, multi-tier fallback, and token cost tracking",
                "proposal_type": "strategic_initiative",
                "proposer_id": "BossAI",
                "proposer_role": "Chief Architect",
                "parameters": {
                    "budget_usd": 25.0,
                    "tasks": [
                        {
                            "title": "Model Routing Config",
                            "objective": "Define YAML tier fallback schema",
                            "scope": "Architecture",
                            "acceptance_criteria": "Tests pass",
                        },
                        {
                            "title": "Circuit Breaker Middleware",
                            "objective": "Prevent cascading LLM failure",
                            "scope": "Core",
                            "acceptance_criteria": "Unit tests pass",
                        },
                    ],
                },
                "quorum_threshold": 0.4,
                "approval_threshold": 0.5,
                "auto_discuss": True,
            },
        )
        assert prop_res.status_code == 200
        prop_data = prop_res.json()
        proposal_id = prop_data["proposal_id"]
        assert prop_data["status"] == "discussion"

        # 3. Deliberate Proposal
        delib_res = await client.post(f"/governance/proposals/{proposal_id}/deliberate")
        assert delib_res.status_code == 200
        delib_data = delib_res.json()
        assert len(delib_data["deliberation_history"]) >= 3
        speakers = [s["speaker_id"] for s in delib_data["deliberation_history"]]
        assert "BossAI" in speakers

        # 4. Open Formal Voting
        open_res = await client.post(f"/governance/proposals/{proposal_id}/open-voting")
        assert open_res.status_code == 200
        assert open_res.json()["status"] == "voting"

        # 5. Cast Votes (3 votes: BossAI=Aye, HEAD-ENG=Aye, HEAD-SEC=Aye)
        v1_res = await client.post(
            f"/governance/proposals/{proposal_id}/vote",
            json={
                "voter_id": "BossAI",
                "choice": "aye",
                "rationale": "High priority strategic initiative",
            },
        )
        assert v1_res.status_code == 200
        assert v1_res.json()["choice"] == "aye"

        v2_res = await client.post(
            f"/governance/proposals/{proposal_id}/vote",
            json={
                "voter_id": "HEAD-ENG",
                "choice": "aye",
                "rationale": "Improves system resilience",
            },
        )
        assert v2_res.status_code == 200

        v3_res = await client.post(
            f"/governance/proposals/{proposal_id}/vote",
            json={
                "voter_id": "HEAD-SEC",
                "choice": "aye",
                "rationale": "API key protection and rate limiting verified",
            },
        )
        assert v3_res.status_code == 200

        # 6. Finalize Voting -> Quorum reached and Approved
        finalize_res = await client.post(f"/governance/proposals/{proposal_id}/finalize")
        assert finalize_res.status_code == 200
        final_data = finalize_res.json()
        assert final_data["status"] == "approved"
        assert final_data["ledger_entry"] is not None
        assert final_data["ledger_entry"]["decision"] == "APPROVED"
        assert len(final_data["votes"]) == 3

        # 7. Execute Proposal -> Creates DAG Tasks
        exec_res = await client.post(f"/governance/proposals/{proposal_id}/execute")
        assert exec_res.status_code == 200
        exec_data = exec_res.json()
        assert exec_data["status"] == "executed"
        assert len(exec_data["tasks"]) == 2
        assert exec_data["proposal"]["status"] == "executed"

        # 8. Query Decision Ledger
        ledger_res = await client.get("/governance/ledger")
        assert ledger_res.status_code == 200
        entries = ledger_res.json()
        assert len(entries) >= 1
        assert entries[0]["sequence_number"] == 1
        assert entries[0]["decision"] == "APPROVED"
        assert len(entries[0]["entry_hash"]) == 64
        assert entries[0]["execution_receipt"] is not None
        assert entries[0]["execution_receipt"]["status"] == "success"
        assert len(entries[0]["execution_receipt"]["task_ids"]) == 2

        # 9. Verify Cryptographic Ledger Chain Integrity
        verify_res = await client.get("/governance/ledger/verify")
        assert verify_res.status_code == 200
        verify_data = verify_res.json()
        assert verify_data["is_valid"] is True
        assert verify_data["total_entries"] >= 1
        assert verify_data["error"] is None


@pytest.mark.asyncio
async def test_governance_api_quorum_failure_and_veto_paths(async_session: AsyncSession):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Quorum Failure Path: Set high quorum threshold (80%)
        p_res = await client.post(
            "/governance/proposals",
            json={
                "title": "Low Participation Proposal",
                "description": "Proposal with minimal voter turnout",
                "proposal_type": "budget_allocation",
                "quorum_threshold": 0.8,  # Needs 80% (6/7 members)
                "approval_threshold": 0.5,
                "auto_discuss": False,
            },
        )
        p_data = p_res.json()
        p_id = p_data["proposal_id"]

        # Transition DRAFT -> DISCUSSION -> VOTING
        await client.post(f"/governance/proposals/{p_id}/deliberate")
        await client.post(f"/governance/proposals/{p_id}/open-voting")

        # Cast only 1 vote
        await client.post(
            f"/governance/proposals/{p_id}/vote",
            json={"voter_id": "BossAI", "choice": "aye"},
        )

        # Finalize voting -> Must be REJECTED due to lack of quorum!
        fin_res = await client.post(f"/governance/proposals/{p_id}/finalize")
        assert fin_res.status_code == 200
        assert fin_res.json()["status"] == "rejected"
        assert "Quorum not reached" in fin_res.json()["ledger_entry"]["rationale"]

        # 2. Executive Veto Path
        veto_prop_res = await client.post(
            "/governance/proposals",
            json={
                "title": "Unauthorized Access Proposal",
                "description": "Bypass security checks for rapid deploy",
                "proposal_type": "emergency_action",
            },
        )
        veto_id = veto_prop_res.json()["proposal_id"]

        veto_res = await client.post(
            f"/governance/proposals/{veto_id}/veto",
            json={
                "reason": "Host privilege escalation prohibited under company charter",
                "vetoed_by": "BossAI",
            },
        )
        assert veto_res.status_code == 200
        assert veto_res.json()["status"] == "vetoed"
        assert veto_res.json()["ledger_entry"]["decision"] == "VETOED"

        # Attempting to execute vetoed proposal must return 400
        bad_exec_res = await client.post(f"/governance/proposals/{veto_id}/execute")
        assert bad_exec_res.status_code == 400
        assert "must be APPROVED" in bad_exec_res.json()["detail"]

        # 3. Proposal Cancellation Path
        cancel_prop_res = await client.post(
            "/governance/proposals",
            json={
                "title": "Aborted Infrastructure Trial",
                "description": "Will be cancelled before voting finishes",
                "auto_discuss": True,
            },
        )
        c_id = cancel_prop_res.json()["proposal_id"]

        cancel_res = await client.post(
            f"/governance/proposals/{c_id}/cancel",
            json={
                "reason": "Superseded by alternate strategy",
                "cancelled_by": "BossAI",
            },
        )
        assert cancel_res.status_code == 200
        c_data = cancel_res.json()
        assert c_data["status"] == "cancelled"
        assert c_data["ledger_entry"]["decision"] == "CANCELLED"

        # 4. Unauthorized Voter Rejection Path
        vote_prop_res = await client.post(
            "/governance/proposals",
            json={
                "title": "Vote Security Verification",
                "description": "Ensure non-committee member cannot vote",
            },
        )
        v_prop_id = vote_prop_res.json()["proposal_id"]
        await client.post(f"/governance/proposals/{v_prop_id}/open-voting")

        unauth_vote_res = await client.post(
            f"/governance/proposals/{v_prop_id}/vote",
            json={
                "voter_id": "ANONYMOUS_INTRUDER",
                "choice": "aye",
            },
        )
        assert unauth_vote_res.status_code == 400
        assert "not an active member of the Boardroom Committee" in unauth_vote_res.json()["detail"]

