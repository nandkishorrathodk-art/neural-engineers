"""
Unit tests for ASTRepoMap & Context Explorer.
"""

from __future__ import annotations

from pathlib import Path

from neural.cli.ast_repomap import ASTRepoMap


def test_ast_repomap_parsing(tmp_path: Path):
    sample_file = tmp_path / "sample_service.py"
    sample_file.write_text(
        "\"\"\"Sample service module docstring.\"\"\"\n\n"
        "import os\n"
        "from typing import Optional\n\n"
        "class UserService:\n"
        "    \"\"\"Manages user entities.\"\"\"\n"
        "    def __init__(self, db_url: str):\n"
        "        self.db_url = db_url\n\n"
        "    async def get_user(self, user_id: str) -> Optional[dict]:\n"
        "        return {'id': user_id}\n\n"
        "def helper_func(count: int = 10) -> int:\n"
        "    \"\"\"Helper calculation.\"\"\"\n"
        "    return count * 2\n",
        encoding="utf-8",
    )

    repo_map = ASTRepoMap(root_dir=tmp_path)
    record = repo_map.parse_file(sample_file)

    assert record.docstring == "Sample service module docstring."
    assert len(record.classes) == 1
    assert record.classes[0]["name"] == "UserService"
    assert len(record.classes[0]["methods"]) == 2
    assert record.classes[0]["methods"][1]["name"] == "get_user"
    assert record.classes[0]["methods"][1]["is_async"] is True

    assert len(record.functions) == 1
    assert record.functions[0]["name"] == "helper_func"
    assert "os" in record.imports
    assert "typing.Optional" in record.imports


def test_ast_repomap_symbol_search(tmp_path: Path):
    file_a = tmp_path / "auth.py"
    file_a.write_text("class Authenticator:\n    def verify(self): pass\n", encoding="utf-8")
    file_b = tmp_path / "token.py"
    file_b.write_text("def generate_token(sub: str): pass\n", encoding="utf-8")

    repo_map = ASTRepoMap(root_dir=tmp_path)
    repo_map.scan_repository()

    auth_syms = repo_map.search_symbols("auth")
    assert any(s.name == "Authenticator" for s in auth_syms)

    token_syms = repo_map.search_symbols("token")
    assert any(s.name == "generate_token" for s in token_syms)


def test_ast_repomap_render_summary(tmp_path: Path):
    f = tmp_path / "math_mod.py"
    f.write_text("def add(a: int, b: int) -> int: return a + b\n", encoding="utf-8")

    repo_map = ASTRepoMap(root_dir=tmp_path)
    summary = repo_map.render_summary()
    assert "math_mod.py" in summary
    assert "add" in summary


def test_ast_repomap_search_by_filepath_and_docstring(tmp_path: Path):
    file_routes = tmp_path / "auth_routes.py"
    file_routes.write_text(
        "\"\"\"Routes for user authentication.\"\"\"\n\n"
        "def handle_login():\n"
        "    \"\"\"Process login credentials.\"\"\"\n"
        "    return True\n",
        encoding="utf-8",
    )
    repo_map = ASTRepoMap(root_dir=tmp_path)
    repo_map.scan_repository()

    # Search for "routes" should match handle_login because it's in auth_routes.py
    matched = repo_map.search_symbols("routes")
    assert any(s.name == "handle_login" for s in matched)

    # Search for "credentials" should match handle_login because of docstring
    matched_doc = repo_map.search_symbols("credentials")
    assert any(s.name == "handle_login" for s in matched_doc)


def test_ast_repomap_syntax_error_resilience(tmp_path: Path):
    broken_file = tmp_path / "broken.py"
    broken_file.write_text("def broken_syntax(:\n    return\n", encoding="utf-8")

    repo_map = ASTRepoMap(root_dir=tmp_path)
    record = repo_map.parse_file(broken_file)
    assert record.docstring is None
    assert record.classes == []
    assert record.functions == []
