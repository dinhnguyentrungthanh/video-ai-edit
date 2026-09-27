import sys

from biliflow.cli import main

try:
    raise SystemExit(main())
except (FileNotFoundError, RuntimeError, ValueError) as error:
    print(f"Lỗi: {error}", file=sys.stderr)
    raise SystemExit(2)
