"""Build dist/functions.zip: the Function app plus the shared `app` and `ingestion` packages.

Deploy: az functionapp deployment source config-zip -g rg-azrag-dev -n <func>
        --src dist/functions.zip
(The platform installs requirements.txt remotely; torch/chromadb are NOT needed in the cloud.)
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "functions.zip"
INCLUDE_DIRS = ["app", "ingestion"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
# Local-only code paths that import heavy dependencies and are never used in the cloud.
SKIP_FILES = {"app/providers/local.py", "app/rag/retrieval.py", "app/cli.py"}


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ROOT / "ingestion" / "function_app.py", "function_app.py")
        zf.write(ROOT / "ingestion" / "host.json", "host.json")
        zf.write(ROOT / "ingestion" / "requirements.txt", "requirements.txt")
        for d in INCLUDE_DIRS:
            for path in sorted((ROOT / d).rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                if (
                    SKIP_PARTS & set(path.parts)
                    or rel in SKIP_FILES
                    or rel.endswith("function_app.py")
                ):
                    continue
                zf.write(path, rel)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
