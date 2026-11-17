"""Package the extension plus its macOS installer; never bundle local state."""
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent
FILES = ["README.md", "PRIVACY.md", "LICENSE", "install.command", "requirements.txt", "requirements.lock.txt",
         "native/host.py", "native/install.py", "previews/popup.png"]
FILES += [str(path.relative_to(ROOT)) for path in (ROOT / "extension").rglob("*") if path.is_file() and path.name != ".DS_Store"]
OUTPUT = ROOT / "dist/offlinevideo.zip"
OUTPUT.parent.mkdir(exist_ok=True)
with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
    for name in sorted(FILES):
        archive.write(ROOT / name, "OfflineVideo/" + name)
version = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
print(f"Packaged OfflineVideo {version}: {OUTPUT.name} ({OUTPUT.stat().st_size:,} bytes)")
