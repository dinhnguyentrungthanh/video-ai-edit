"""Scan stage key sources of HEAD vs 11a059c: which files they hash, and whether any changed."""
import hashlib
import subprocess
import sys
from pathlib import Path

root = Path.cwd().resolve()
sys.path.insert(0, str(root / "src"))
from biliflow.cache_dependencies import STAGE_MODULES, stage_source_paths  # noqa: E402

changed = {(root / name).resolve() for name in subprocess.run(
    ["git", "diff", "--name-only", "11a059c", "HEAD"], capture_output=True, text=True).stdout.split()}
print("changed:", sorted(path.relative_to(root).as_posix() for path in changed))
for stage in sorted(STAGE_MODULES):
    paths = sorted({path.resolve() for path in stage_source_paths(root, stage)})
    now, then = hashlib.sha256(), hashlib.sha256()
    for path in paths:
        name = path.relative_to(root).as_posix()
        now.update(name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n"))
        then.update(name.encode() + b"\0" + subprocess.run(["git", "show", f"11a059c:{name}"], capture_output=True).stdout)
    print(f"{stage}: {len(paths)} files, {len(set(paths) & changed)} changed, same bytes as 11a059c: {now.digest() == then.digest()}")
