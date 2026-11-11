# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the `tt-sidecar` binary the Tauri shell launches (ADR-0038). NOT run in CI: build it with
# `apps/desktop/sidecar/build.sh`. The entry module is apps/cli/tt_cli/sidecar.py (the same code as the `tt-sidecar` console script).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[2]  # noqa: F821 - SPECPATH is injected by PyInstaller

datas = []
for pkg in ("tt_client", "tt_schema", "dateparser", "jsonschema", "jsonschema_specifications", "tzdata"):
    datas += collect_data_files(pkg)  # roles.yaml, JSON schemas, dateparser locale data, tz database

hiddenimports = []
for pkg in ("tt_client", "tt_schema", "tt_cli", "keyring.backends", "dateparser"):
    hiddenimports += collect_submodules(pkg)

a = Analysis(  # noqa: F821
    [str(ROOT / "apps" / "cli" / "tt_cli" / "sidecar.py")],
    pathex=[
        str(ROOT / "apps" / "cli"),
        str(ROOT / "packages" / "client_core" / "src"),
        str(ROOT / "packages" / "schema" / "src"),
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="tt-sidecar",
    console=True,  # stdin/stdout JSON; the Tauri shell hides the window
    upx=False,
)
