"""Package only integration source; never include local login sessions."""
from pathlib import Path
import json
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
out = root / "dist"
out.mkdir(exist_ok=True)
version = json.loads((root / "custom_components/autocheck_vpn/manifest.json").read_text(encoding="utf-8"))["version"]
target = out / f"autocheck_vpn-{version}.zip"
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for file in sorted((root / "custom_components/autocheck_vpn").rglob("*")):
        if file.is_file() and file.suffix in {".py", ".json"} and "__pycache__" not in file.parts:
            archive.write(file, file.relative_to(root))
    for name in ("README.md", "VALIDATION.md", "tools/verify.py", "tools/browser_login.py"):
        archive.write(root / name, name)
print(target)
