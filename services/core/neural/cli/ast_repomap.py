"""
AST Repository Map & Codebase Context Explorer for ANCOS CLI.
Parses Python source files into an AST representation, extracts classes, functions,
methods, docstrings, and signatures, and generates compact repository context maps.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str  # "class", "function", "method"
    file_path: str
    line_number: int
    signature: str
    docstring: Optional[str] = None
    parent_class: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileASTRecord:
    file_path: str
    docstring: Optional[str]
    classes: list[dict[str, Any]]
    functions: list[dict[str, Any]]
    imports: list[str]


class ASTRepoMap:
    """
    Codebase AST Symbol Extractor and Context Map.
    """

    DEFAULT_EXCLUDES = {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".next",
        ".pytest_cache",
        "dist",
        "build",
        "data",
        "migrations",
    }

    def __init__(self, root_dir: Optional[Path | str] = None) -> None:
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd()
        self.files_map: dict[str, FileASTRecord] = {}
        self.symbols: list[SymbolRecord] = []

    def scan_repository(
        self,
        max_files: int = 150,
        subpath: Optional[str] = None,
        custom_excludes: Optional[set[str]] = None,
    ) -> dict[str, FileASTRecord]:
        """
        Scans Python source files under root_dir (or subpath) and parses AST structures.
        """
        target_dir = (self.root_dir / subpath).resolve() if subpath else self.root_dir
        excludes = self.DEFAULT_EXCLUDES | (custom_excludes or set())

        self.files_map.clear()
        self.symbols.clear()
        file_count = 0

        for path in sorted(target_dir.rglob("*.py")):
            try:
                rel = path.relative_to(self.root_dir)
                rel_parts = set(rel.parts)
            except ValueError:
                rel = path
                rel_parts = set(path.parts)

            if any(ex in rel_parts for ex in excludes):
                continue

            try:
                record = self.parse_file(path)
                rel_str = str(rel).replace("\\", "/")
                self.files_map[rel_str] = record
                file_count += 1
                if file_count >= max_files:
                    break
            except Exception:
                continue

        return self.files_map

    def parse_file(self, file_path: Path) -> FileASTRecord:
        """Parses a single file into AST classes, functions, and imports."""
        source = file_path.read_text(encoding="utf-8", errors="replace")
        rel_path = str(
            file_path.relative_to(self.root_dir)
            if file_path.is_relative_to(self.root_dir)
            else file_path
        ).replace("\\", "/")

        try:
            tree = ast.parse(source, filename=str(file_path))
        except Exception:
            return FileASTRecord(
                file_path=rel_path,
                docstring=None,
                classes=[],
                functions=[],
                imports=[],
            )

        docstring = ast.get_docstring(tree)
        classes: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        imports: list[str] = []

        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}" if module else alias.name)

            elif isinstance(node, ast.ClassDef):
                cls_doc = ast.get_docstring(node)
                bases = [self._format_node(b) for b in node.bases]
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_doc = ast.get_docstring(item)
                        sig = self._format_signature(item)
                        methods.append({
                            "name": item.name,
                            "signature": sig,
                            "line": item.lineno,
                            "docstring": m_doc,
                            "is_async": isinstance(item, ast.AsyncFunctionDef),
                        })
                        self.symbols.append(
                            SymbolRecord(
                                name=item.name,
                                kind="method",
                                file_path=rel_path,
                                line_number=item.lineno,
                                signature=sig,
                                docstring=m_doc,
                                parent_class=node.name,
                            )
                        )

                cls_data = {
                    "name": node.name,
                    "bases": bases,
                    "line": node.lineno,
                    "docstring": cls_doc,
                    "methods": methods,
                }
                classes.append(cls_data)
                self.symbols.append(
                    SymbolRecord(
                        name=node.name,
                        kind="class",
                        file_path=rel_path,
                        line_number=node.lineno,
                        signature=f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}",
                        docstring=cls_doc,
                    )
                )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_doc = ast.get_docstring(node)
                sig = self._format_signature(node)
                fn_data = {
                    "name": node.name,
                    "signature": sig,
                    "line": node.lineno,
                    "docstring": fn_doc,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                }
                functions.append(fn_data)
                self.symbols.append(
                    SymbolRecord(
                        name=node.name,
                        kind="function",
                        file_path=rel_path,
                        line_number=node.lineno,
                        signature=sig,
                        docstring=fn_doc,
                    )
                )

        return FileASTRecord(
            file_path=rel_path,
            docstring=docstring,
            classes=classes,
            functions=functions,
            imports=imports,
        )

    def search_symbols(self, query: str) -> list[SymbolRecord]:
        """Searches classes, methods, and functions matching query substring."""
        q = query.strip().lower()
        if not self.symbols and not self.files_map:
            self.scan_repository(max_files=100)

        results: list[SymbolRecord] = []
        for sym in self.symbols:
            if (
                q in sym.name.lower()
                or (sym.parent_class and q in sym.parent_class.lower())
                or q in sym.file_path.lower()
                or (sym.docstring and q in sym.docstring.lower())
            ):
                results.append(sym)
        return results

    def render_summary(self, max_files: int = 30) -> str:
        """Renders compact summary representation of key codebase structures."""
        if not self.files_map:
            self.scan_repository(max_files=max_files)

        lines: list[str] = [f"=== AST Repository Map: {self.root_dir.name} ==="]
        for rel_path, rec in list(self.files_map.items())[:max_files]:
            lines.append(f"\n[{rel_path}]")
            if rec.classes:
                for c in rec.classes:
                    bases_str = f"({', '.join(c['bases'])})" if c["bases"] else ""
                    lines.append(f"  class {c['name']}{bases_str}:")
                    for m in c["methods"]:
                        lines.append(f"    - def {m['signature']}")
            if rec.functions:
                for fn in rec.functions:
                    prefix = "async def " if fn["is_async"] else "def "
                    lines.append(f"  {prefix}{fn['signature']}")

        return "\n".join(lines)

    def _format_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = []
        for a in node.args.args:
            arg_str = a.arg
            if a.annotation:
                arg_str += f": {self._format_node(a.annotation)}"
            args.append(arg_str)

        returns_str = ""
        if node.returns:
            returns_str = f" -> {self._format_node(node.returns)}"

        return f"{node.name}({', '.join(args)}){returns_str}"

    def _format_node(self, node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", "") or getattr(node, "attr", "") or str(type(node).__name__)
