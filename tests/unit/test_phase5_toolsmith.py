import pytest
from neural.db.repositories import ToolRepository
from neural.sandboxes.workstation import WorkstationSandbox
from neural.tools.engine import ToolsmithEngine
from neural.tools.registry import ToolRegistry
from neural.tools.safety import ToolASTSafetyValidator, ToolSafetyReport
from sqlalchemy.ext.asyncio import AsyncSession


def test_ast_safety_validator_blocks_dangerous_calls():
    validator = ToolASTSafetyValidator()

    # 1. os.system
    code_os = """
import os
def run_command():
    os.system("ls -la")
"""
    rep1: ToolSafetyReport = validator.validate(code_os)
    assert rep1.passed is False
    assert any("os.system" in v for v in rep1.violations)

    # 2. subprocess
    code_sub = """
import subprocess
def ping():
    subprocess.Popen(["ping", "127.0.0.1"])
"""
    rep2 = validator.validate(code_sub)
    assert rep2.passed is False
    assert any("subprocess" in v for v in rep2.violations)

    # 3. eval/exec
    code_eval = """
def dynamic_eval(payload: str):
    return eval(payload)
"""
    rep3 = validator.validate(code_eval)
    assert rep3.passed is False
    assert any("eval()" in v for v in rep3.violations)


def test_ast_safety_validator_detects_credentials():
    validator = ToolASTSafetyValidator()

    code_secret = """
def fetch_data():
    api_key = "sk-1234567890abcdef1234567890abcdef"
    return api_key
"""
    report = validator.validate(code_secret)
    assert report.passed is False
    assert any("credential" in v.lower() for v in report.violations)


def test_ast_safety_validator_detects_infinite_loops():
    validator = ToolASTSafetyValidator()

    code_loop = """
def infinite():
    while True:
        pass
"""
    report = validator.validate(code_loop)
    assert report.passed is False
    assert any("infinite while-loop" in v.lower() for v in report.violations)

    # Valid loop with break
    valid_loop = """
def finite_loop():
    x = 0
    while True:
        x += 1
        if x > 10:
            break
    return x
"""
    report_valid = validator.validate(valid_loop)
    assert report_valid.passed is True


def test_ast_safety_validator_infers_interfaces():
    validator = ToolASTSafetyValidator()

    code = """
def calculate_metrics(count: int, ratio: float, label: str = "default") -> dict:
    \"\"\"Calculates performance metrics based on count and ratio.\"\"\"
    return {"count": count, "score": count * ratio, "label": label}
"""
    report = validator.validate(code)
    assert report.passed is True
    assert "calculate_metrics" in report.exported_functions
    schema = report.inferred_interface.get("calculate_metrics", {})
    assert schema["type"] == "function"
    params = schema["function"]["parameters"]["properties"]
    assert params["count"]["type"] == "integer"
    assert params["ratio"]["type"] == "number"
    assert params["label"]["type"] == "string"
    assert "count" in schema["function"]["parameters"]["required"]
    assert "ratio" in schema["function"]["parameters"]["required"]
    # default param should not be required
    assert "label" not in schema["function"]["parameters"]["required"]


@pytest.mark.asyncio
async def test_tool_registry_versioning_and_discovery(async_session: AsyncSession):
    repo = ToolRepository(async_session)
    registry = ToolRegistry(tool_repo=repo)

    t1 = await repo.create_tool(
        name="data_parser",
        version="1.0.0",
        code="def parse(): return 1",
        test_code="from tool import parse; assert parse() == 1",
        status="verified",
    )
    t2 = await repo.create_tool(
        name="data_parser",
        version="1.1.0",
        code="def parse(): return 2",
        test_code="from tool import parse; assert parse() == 2",
        status="verified",
    )
    await registry.register(t1)
    await registry.register(t2)

    latest = await registry.get_tool("data_parser", verified_only=True)
    assert latest is not None
    assert latest.version == "1.1.0"

    v1 = await registry.get_tool("data_parser", version="1.0.0")
    assert v1 is not None
    assert v1.version == "1.0.0"

    # Dependency isolation check
    valid, _ = registry.validate_dependencies(["pydantic>=2.0", "requests"])
    assert valid is True

    invalid, violations = registry.validate_dependencies(["subprocess", "os"])
    assert invalid is False
    assert len(violations) >= 2


@pytest.mark.asyncio
async def test_toolsmith_author_and_verify_tool(async_session: AsyncSession):
    repo = ToolRepository(async_session)
    sandbox = WorkstationSandbox()
    engine = ToolsmithEngine(tool_repo=repo, sandbox=sandbox)

    code = """
def add_vectors(a: list, b: list) -> list:
    \"\"\"Element-wise addition of two vectors.\"\"\"
    return [x + y for x, y in zip(a, b)]
"""
    test_code = """
from tool import add_vectors

v1 = [1, 2, 3]
v2 = [4, 5, 6]
res = add_vectors(v1, v2)
assert res == [5, 7, 9]
print("VECTOR_ADD_TEST_PASSED")
"""
    tool, receipt = await engine.author_tool(
        name="vector_ops",
        code=code,
        test_code=test_code,
        description="Vector addition utility",
        version="1.0.0",
        category="math",
        auto_test=True,
    )

    assert tool.status == "verified"
    assert tool.safety_status == "passed"
    assert receipt is not None
    assert receipt.exit_code == 0
    assert "VECTOR_ADD_TEST_PASSED" in receipt.stdout

    # Test dynamic execution
    exec_res = await engine.execute_tool(
        tool_name_or_id="vector_ops",
        function_name="add_vectors",
        arguments={"a": [10, 20], "b": [30, 40]},
    )
    assert exec_res["status"] == "success"
    assert exec_res["result"] == [40, 60]


@pytest.mark.asyncio
async def test_toolsmith_rejects_failing_tool(async_session: AsyncSession):
    repo = ToolRepository(async_session)
    sandbox = WorkstationSandbox()
    engine = ToolsmithEngine(tool_repo=repo, sandbox=sandbox)

    code = """
def broken_fn():
    return 10
"""
    test_code = """
from tool import broken_fn
assert broken_fn() == 42
"""
    tool, receipt = await engine.author_tool(
        name="broken_tool",
        code=code,
        test_code=test_code,
        auto_test=True,
    )
    assert tool.status == "rejected"
    assert receipt is not None
    assert receipt.exit_code != 0


def test_ast_safety_validator_blocks_from_import_and_aliases():
    validator = ToolASTSafetyValidator()

    # 1. from os import system
    code_from_os = """
from os import system
def pwn():
    system("id")
"""
    rep1 = validator.validate(code_from_os)
    assert rep1.passed is False
    assert any("os.system" in v for v in rep1.violations)

    # 2. import os as o; o.system(...)
    code_alias = """
import os as o
def pwn():
    o.system("id")
"""
    rep2 = validator.validate(code_alias)
    assert rep2.passed is False
    assert any("os.system" in v for v in rep2.violations)

    # 3. from shutil import rmtree
    code_shutil = """
from shutil import rmtree
def clean():
    rmtree("/tmp")
"""
    rep3 = validator.validate(code_shutil)
    assert rep3.passed is False
    assert any("shutil.rmtree" in v for v in rep3.violations)

    # 4. importlib and builtins
    code_importlib = """
import importlib
def load():
    return importlib.import_module("os")
"""
    rep4 = validator.validate(code_importlib)
    assert rep4.passed is False
    assert any("importlib" in v for v in rep4.violations)

    # 5. getattr dangerous call
    code_getattr = """
import sys
def hack():
    return getattr(sys, "modules")
"""
    rep5 = validator.validate(code_getattr)
    assert rep5.passed is False
    assert any("getattr" in v for v in rep5.violations)

    # 6. Introspection __subclasses__
    code_sub = """
def escape():
    return ().__class__.__base__.__subclasses__()
"""
    rep6 = validator.validate(code_sub)
    assert rep6.passed is False
    assert any("__subclasses__" in v for v in rep6.violations)


def test_ast_safety_validator_nested_infinite_loop_detection():
    validator = ToolASTSafetyValidator()

    # While loop with inner function return should NOT mask infinite loop
    code_inner_func = """
def bad_loop():
    while True:
        def helper():
            return 42
"""
    rep1 = validator.validate(code_inner_func)
    assert rep1.passed is False
    assert any("infinite while-loop" in v.lower() for v in rep1.violations)

    # While loop with inner for loop break should NOT mask infinite loop
    code_inner_break = """
def bad_loop_2():
    while True:
        for x in [1, 2]:
            break
"""
    rep2 = validator.validate(code_inner_break)
    assert rep2.passed is False
    assert any("infinite while-loop" in v.lower() for v in rep2.violations)


def test_ast_safety_validator_anthropic_and_fine_grained_pat_detection():
    validator = ToolASTSafetyValidator()

    code_anthropic = 'key = "sk-ant-api03-abcdef1234567890abcdef1234567890"'
    rep1 = validator.validate(code_anthropic)
    assert rep1.passed is False
    assert any("credential" in v.lower() for v in rep1.violations)

    code_pat = 'token = "github_pat_11ABCD_1234567890abcdef1234567890"'
    rep2 = validator.validate(code_pat)
    assert rep2.passed is False
    assert any("credential" in v.lower() for v in rep2.violations)


@pytest.mark.asyncio
async def test_toolsmith_executes_async_function(async_session: AsyncSession):
    repo = ToolRepository(async_session)
    sandbox = WorkstationSandbox()
    engine = ToolsmithEngine(tool_repo=repo, sandbox=sandbox)

    code = """
async def multiply_async(x: int, y: int) -> int:
    \"\"\"Async multiplication utility.\"\"\"
    return x * y
"""
    test_code = """
import asyncio
from tool import multiply_async

res = asyncio.run(multiply_async(6, 7))
assert res == 42
print("ASYNC_TEST_OK")
"""
    tool, receipt = await engine.author_tool(
        name="async_math",
        code=code,
        test_code=test_code,
        auto_test=True,
    )
    assert tool.status == "verified"
    assert receipt is not None
    assert receipt.exit_code == 0

    # Dynamic execution of async tool function
    exec_res = await engine.execute_tool(
        tool_name_or_id="async_math",
        function_name="multiply_async",
        arguments={"x": 9, "y": 9},
    )
    assert exec_res["status"] == "success"
    assert exec_res["result"] == 81

