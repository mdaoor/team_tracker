"""Phase 13 R3.6 (review T9): the container images are multi-stage, non-root and not built from a moving tag."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
IMAGES = [ROOT / "apps" / "server" / "Dockerfile", ROOT / "apps" / "llm_gateway" / "Dockerfile"]


def test_images_are_multistage_nonroot_and_pinned() -> None:
    for df in IMAGES:
        text = df.read_text()
        assert len(re.findall(r"^FROM ", text, re.M)) >= 3, (
            f"{df.name}: needs a build stage and a runtime stage"
        )
        assert "AS runtime" in text and "AS builder" in text
        assert re.search(r"^USER 10001(:10001)?$", text, re.M), f"{df}: runs as root"
        assert ":latest" not in text, f"{df}: moving tag"
        assert re.search(r"astral-sh/uv:\d+\.\d+\.\d+", text), f"{df}: uv not pinned to a release"
        # 13 M5: a tag can be re-pushed; the uv image must also be pinned by digest in every FROM / COPY --from that names it
        refs = re.findall(r"^(?:FROM|COPY\s+--from=)\s*(\S*astral-sh/uv\S*)", text, re.M)
        assert refs, f"{df}: no uv image reference found"
        assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in refs), f"{df}: uv pinned by tag only"
        runtime = text.split("AS runtime", 1)[1]
        assert "uv sync" not in runtime and "pip install" not in runtime, (
            "no build tooling in the runtime image"
        )


def test_dockerignore_keeps_secrets_and_junk_out_of_the_context() -> None:
    lines = set((ROOT / ".dockerignore").read_text().split())
    assert {".git", ".env", "**/node_modules", ".venv"} <= lines
