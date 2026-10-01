"""Conservative local-code dependency scopes for scan artifact caching."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


STAGE_MODULES = {
    "adult": ("scanner",),
    "verify_adult": ("adult_verification",),
    "text": ("textscan",),
    "visual_logo": ("visual_logo_scanner",),
    "localize_logo": ("ad_candidate_pipeline", "brand_memory", "florence_regions"),
    "confirm_violence": ("vlm_confirmation",),
    "animation_safety": ("animation_safety_scanner",),
    "live_safety": ("live_safety_scanner",),
    # scan-content dispatches by style/kind; retain all possible implementations.
    "gore": ("content_scanner", "animation_safety_scanner", "violence_scanner"),
    "violence": ("content_scanner", "animation_safety_scanner", "violence_scanner"),
}


def stage_source_paths(root: Path, stage: str) -> tuple[Path, ...]:
    """Follow eager AND lazy local imports; uncertain analysis hashes all source.

    CLI/entrypoint files are hashed in full but are dispatch boundaries: following
    every CLI command would pull dashboard/render modules into every detector.
    Their edits still invalidate all stages. Scanner imports have no boundary.
    """
    source = root / "src" / "biliflow"
    all_source = tuple(sorted(source.rglob("*.py")))
    if stage not in STAGE_MODULES:
        return all_source
    boundaries = {source / name for name in (
        "__init__.py", "__main__.py", "cli.py", "stage_cache.py", "cache_dependencies.py",
    )}
    pending = [source / (name + ".py") for name in (*STAGE_MODULES[stage], "license_policy")]
    if stage == "localize_logo":
        pending.append(root / "scripts" / "localize_visual_logo_report.py")
    visited = set(boundaries)

    def module_path(module):
        parts = module.split(".")
        base = root / "src" / Path(*parts)
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        raise ValueError(f"Unresolved local dependency: {module}")

    try:
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec"}
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "exec_module"}
                ):
                    raise ValueError("Dynamic dependency requires full fingerprint")
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        package = ".".join(path.relative_to(root / "src").with_suffix("").parts[:-1])
                        module = importlib.util.resolve_name("." * node.level + module, package)
                    modules = [module]
                    # Handles `from biliflow import scanner` as well as symbols.
                    for alias in node.names:
                        child = root / "src" / Path(*(module + "." + alias.name).split("."))
                        if child.with_suffix(".py").is_file() or (child / "__init__.py").is_file():
                            modules.append(module + "." + alias.name)
                if any(module == "importlib" or module.startswith("importlib.") for module in modules):
                    raise ValueError("Importlib dependency requires full fingerprint")
                for module in modules:
                    if module == "biliflow" or module.startswith("biliflow."):
                        dependency = module_path(module)
                        pending.append(dependency)
                        # Package initializers can execute code too.
                        for parent in dependency.parents:
                            if parent == root / "src":
                                break
                            initializer = parent / "__init__.py"
                            if initializer.is_file():
                                pending.append(initializer)
    except (OSError, SyntaxError, ValueError, ImportError):
        return tuple(sorted(set(all_source) | boundaries | {
            root / "scripts" / "localize_visual_logo_report.py",
        }))
    return tuple(sorted(visited))
