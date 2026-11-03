import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_toolsmith_api_endpoints(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Author a new tool
        author_payload = {
            "name": "base64_helper",
            "code": """
import base64

def encode_text(text: str) -> str:
    \"\"\"Encodes a string to standard base64.\"\"\"
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")

def decode_text(encoded: str) -> str:
    \"\"\"Decodes base64 string to utf-8 text.\"\"\"
    return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
""",
            "test_code": """
from tool import encode_text, decode_text

encoded = encode_text("hello_world")
assert decode_text(encoded) == "hello_world"
print("BASE64_VERIFIED")
""",
            "description": "Base64 encoding/decoding utility",
            "version": "1.0.0",
            "category": "utility",
            "auto_test": True,
        }
        res_auth = await client.post("/toolsmith/author", json=author_payload)
        assert res_auth.status_code == 200, res_auth.text
        data = res_auth.json()
        assert data["name"] == "base64_helper"
        assert data["status"] == "verified"
        assert data["safety_status"] == "passed"
        tool_id = data["tool_id"]

        # 2. List tools
        res_list = await client.get("/toolsmith/tools?verified_only=true")
        assert res_list.status_code == 200
        tools = res_list.json()
        assert any(t["tool_id"] == tool_id for t in tools)

        # 3. Get tool details
        res_get = await client.get(f"/toolsmith/tools/{tool_id}")
        assert res_get.status_code == 200
        assert res_get.json()["tool_id"] == tool_id

        # 4. Execute tool function
        exec_payload = {
            "tool_name_or_id": "base64_helper",
            "function_name": "encode_text",
            "arguments": {"text": "ANCOS-PHASE-5"},
        }
        res_exec = await client.post("/toolsmith/execute", json=exec_payload)
        assert res_exec.status_code == 200
        exec_data = res_exec.json()
        assert exec_data["status"] == "success"
        assert exec_data["result"] == "QU5DT1MtUEhBU0UtNQ=="

        # 5. Deprecate tool
        res_del = await client.delete(f"/toolsmith/tools/{tool_id}")
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "deprecated"


@pytest.mark.asyncio
async def test_methods_api_endpoints(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Synthesize candidate method
        synth_payload = {
            "name": "chain_of_verification_fastapi",
            "objective": "Eliminate API hallucinations through empirical self-checks",
            "strategy_type": "chain_of_verification",
            "domain": "backend_engineering",
        }
        res_synth = await client.post("/methods/synthesize", json=synth_payload)
        assert res_synth.status_code == 200, res_synth.text
        method_data = res_synth.json()
        assert method_data["name"] == "chain_of_verification_fastapi"
        assert method_data["status"] == "candidate"
        method_id = method_data["method_id"]

        # 2. Benchmark candidate method
        res_bmk = await client.post(f"/methods/{method_id}/benchmark", json={})
        assert res_bmk.status_code == 503, res_bmk.text

        # 3. Promotion remains blocked without executor-backed benchmark evidence.
        res_promote = await client.post(f"/methods/{method_id}/promote", json={"force": True})
        assert res_promote.status_code == 503

        # 4. View active methodology catalog
        res_cat = await client.get("/methods/catalog")
        assert res_cat.status_code == 200
        catalog = res_cat.json()
        assert not any(m["method_id"] == method_id for m in catalog)

        # 5. Get method details
        res_get = await client.get(f"/methods/{method_id}")
        assert res_get.status_code == 200
        assert res_get.json()["method_id"] == method_id
        assert res_get.json()["status"] == "candidate"


@pytest.mark.asyncio
async def test_meta_learning_api_endpoints(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Store into 4-tier memory
        mem_payload = {
            "category": "procedure",
            "source": "api_test",
            "content": "Standard procedure: always run AST validation before executing agent code",
            "confidence": 0.98,
        }
        res_mem_post = await client.post("/meta-learning/memory", json=mem_payload)
        assert res_mem_post.status_code == 200, res_mem_post.text
        assert res_mem_post.json()["category"] == "procedure"

        # 2. Query memory
        res_mem_get = await client.get("/meta-learning/memory?category=procedure")
        assert res_mem_get.status_code == 200
        mems = res_mem_get.json()
        assert len(mems) >= 1

        # 3. Memory stats
        res_stats = await client.get("/meta-learning/memory/stats")
        assert res_stats.status_code == 200
        assert res_stats.json()["total_memories"] >= 1

        # 4. Failure patterns
        res_fp = await client.get("/meta-learning/failure-patterns")
        assert res_fp.status_code == 200
        assert "patterns" in res_fp.json()

        # 5. Optimize prompt
        prompt_payload = {
            "current_prompt": "You are a backend coder.",
            "pattern_type": "ASYNC_SESSION_MISMANAGEMENT",
            "target_role": "Backend Engineer",
        }
        res_opt = await client.post("/meta-learning/optimize-prompt", json=prompt_payload)
        assert res_opt.status_code == 200
        opt_data = res_opt.json()
        assert opt_data["candidate_status"] == "not_evaluated"
        assert opt_data["improvement_pct"] is None
        assert len(opt_data["rules_injected"]) >= 1

        # 6. Trigger autonomous self-improvement cycle
        cycle_payload = {
            "trigger_reason": "integration_test_verification",
            "auto_apply": True,
            "sample_failures": [
                {
                    "task_id": "TASK-INT-01",
                    "title": "Async Session Leak",
                    "verification_notes": "async session error missing await context manager",
                }
            ],
        }
        res_cycle = await client.post("/meta-learning/cycle", json=cycle_payload)
        assert res_cycle.status_code == 200, res_cycle.text
        cycle_data = res_cycle.json()
        assert cycle_data["status"] == "completed"
        assert len(cycle_data["failure_patterns_detected"]) >= 1
        assert len(cycle_data["actions_taken"]) >= 1
        assert cycle_data["memories_stored_count"] >= 1

        # 7. List evolution history
        res_hist = await client.get("/meta-learning/history")
        assert res_hist.status_code == 200
        history = res_hist.json()
        assert len(history) >= 1
        assert history[0]["record_type"] == "meta_learning_cycle"


@pytest.mark.asyncio
async def test_toolsmith_api_blocks_dangerous_ast_and_tests_existing_tool(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Author malicious tool with dangerous import
        malicious_payload = {
            "name": "malicious_tool",
            "code": "from os import system\ndef run(): system('id')",
            "test_code": "assert True",
        }
        res_mal = await client.post("/toolsmith/author", json=malicious_payload)
        assert res_mal.status_code == 200
        data_mal = res_mal.json()
        assert data_mal["status"] == "rejected"
        assert data_mal["safety_status"] == "violated"
        assert any("os.system" in v for v in data_mal["safety_violations"])

        # 2. Author valid tool and re-test it via /test endpoint
        valid_payload = {
            "name": "math_doubler",
            "code": "def double(x: int) -> int: return x * 2",
            "test_code": "from tool import double\nassert double(5) == 10",
        }
        res_valid = await client.post("/toolsmith/author", json=valid_payload)
        assert res_valid.status_code == 200
        assert res_valid.json()["status"] == "verified"

        res_test = await client.post("/toolsmith/test/math_doubler", json={})
        assert res_test.status_code == 200
        assert res_test.json()["status"] == "verified"


@pytest.mark.asyncio
async def test_methods_api_fallback_serialization(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Synthesize method
        res_synth = await client.post("/methods/synthesize", json={
            "name": "fallback_test_method",
            "objective": "Verify serialization resilience",
            "strategy_type": "decomposition_parallel",
        })
        assert res_synth.status_code == 200
        m_data = res_synth.json()

        # Benchmark should serialize cleanly with valid recorded_at
        res_bmk = await client.post(f"/methods/{m_data['method_id']}/benchmark", json={})
        assert res_bmk.status_code == 503

