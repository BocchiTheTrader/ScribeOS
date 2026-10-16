# PyInstaller spec — build with:  pyinstaller packaging/scribeos.spec --noconfirm
# Produces dist/ScribeOS/ScribeOS.exe (one-folder build: starts faster, fewer antivirus false positives).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller

datas = [(str(ROOT / "scribeos" / "web_dist"), "scribeos/web_dist")]
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("scribeos")

PACKAGES = ["webview", "typst", "pycrdt", "argon2", "docx", "ebooklib", "mammoth", "qrcode", "clr_loader", "pythonnet"]
# Offline dictation (optional extra): bundled when installed in the build environment.
for optional in ("faster_whisper", "ctranslate2", "onnxruntime", "av", "sounddevice", "_sounddevice_data", "tokenizers"):
    try:
        __import__(optional)
        PACKAGES.append(optional)
    except ImportError:
        pass

for pkg in PACKAGES:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest", "playwright", "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScribeOS",
    console=False,
    icon=str(ROOT / "packaging" / "scribeos.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="ScribeOS")  # noqa: F821
