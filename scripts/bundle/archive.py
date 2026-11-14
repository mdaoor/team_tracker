"""Pack, inspect and verify bundle archives (.tar.zst or .tar.gz) without unpacking them to disk.

    archive.py pack DIR OUT.tar.zst|OUT.tar.gz     (SHA256SUMS and manifest.json are stored first)
    archive.py manifest FILE                        print manifest.json
    archive.py verify FILE                          stream every member and compare with SHA256SUMS
    archive.py unpack FILE DEST

zstd needs the "zstd" program or the Python package "zstandard" (uv run --with zstandard ...).
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO

FIRST = ("SHA256SUMS", "SHA256SUMS.asc", "manifest.json", "VERSION", "install.sh")


def _zstd_cli() -> str | None:
    return shutil.which("zstd")


@contextmanager
def _reader(path: str) -> Iterator[IO[bytes]]:
    if path.endswith(".gz") or path.endswith(".tgz"):
        with open(path, "rb") as f:
            yield f
        return
    if exe := _zstd_cli():
        p = subprocess.Popen([exe, "-dc", "-q", path], stdout=subprocess.PIPE)  # noqa: S603
        assert p.stdout is not None
        try:
            yield p.stdout
        finally:
            p.stdout.close()
            p.wait()
        return
    import zstandard  # type: ignore[import-not-found]

    with open(path, "rb") as f:
        yield zstandard.ZstdDecompressor().stream_reader(f)


def _open_tar(stream: IO[bytes], path: str) -> tarfile.TarFile:
    mode = "r|gz" if path.endswith((".gz", ".tgz")) else "r|"
    return tarfile.open(fileobj=stream, mode=mode)


def pack(src: str, out: str) -> None:
    top = os.path.basename(os.path.normpath(src))
    names: list[str] = []
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for fn in sorted(files):
            rel = os.path.relpath(os.path.join(root, fn), src)
            names.append(rel)
    names.sort(key=lambda n: (n not in FIRST, n))

    def add_all(tf: tarfile.TarFile) -> None:
        for rel in names:
            tf.add(os.path.join(src, rel), arcname=f"{top}/{rel}", recursive=False)
        # empty directories are not needed; symlinks are added as links by tf.add
    if out.endswith((".gz", ".tgz")):
        with tarfile.open(out, "w:gz", compresslevel=6) as tf:
            add_all(tf)
        return
    if exe := _zstd_cli():
        with open(out, "wb") as f:
            p = subprocess.Popen([exe, "-q", "-T0", "-10", "-c"], stdin=subprocess.PIPE, stdout=f)  # noqa: S603
            assert p.stdin is not None
            with tarfile.open(fileobj=p.stdin, mode="w|") as tf:
                add_all(tf)
            p.stdin.close()
            if p.wait() != 0:
                sys.exit("zstd failed")
        return
    import zstandard  # type: ignore[import-not-found]

    with open(out, "wb") as f:
        with zstandard.ZstdCompressor(level=10, threads=-1).stream_writer(f) as w:
            with tarfile.open(fileobj=w, mode="w|") as tf:  # type: ignore[call-overload]
                add_all(tf)


def manifest(path: str) -> dict:
    with _reader(path) as s, _open_tar(s, path) as tf:
        for m in tf:
            if m.name.endswith("/manifest.json") and m.name.count("/") == 1:
                f = tf.extractfile(m)
                assert f is not None
                return json.loads(f.read())
    sys.exit("manifest.json not found in the archive")


def verify(path: str) -> int:
    sums: dict[str, str] = {}
    seen: dict[str, str] = {}
    with _reader(path) as s, _open_tar(s, path) as tf:
        for m in tf:
            rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
            if rel == "SHA256SUMS":
                f = tf.extractfile(m)
                assert f is not None
                for line in io.TextIOWrapper(f, encoding="utf-8"):
                    h, _, name = line.rstrip("\n").partition("  ")
                    sums[name.lstrip("./")] = h
                continue
            if not m.isfile():
                continue
            f = tf.extractfile(m)
            assert f is not None
            h = hashlib.sha256()
            while chunk := f.read(1 << 20):
                h.update(chunk)
            seen[rel] = h.hexdigest()
    if not sums:
        print("SHA256SUMS missing", file=sys.stderr)
        return 1
    bad = [n for n, h in sums.items() if seen.get(n) != h]
    extra = [n for n in seen if n not in sums and n not in ("SHA256SUMS.asc",)]
    for n in bad:
        print(f"MISMATCH {n}", file=sys.stderr)
    for n in extra:
        print(f"NOT LISTED {n}", file=sys.stderr)
    print(f"{len(sums)} files checked, {len(bad)} mismatched, {len(extra)} not listed")
    return 1 if bad or extra else 0


def unpack(path: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    with _reader(path) as s, _open_tar(s, path) as tf:
        kw = {"filter": "tar"} if sys.version_info >= (3, 12) else {}
        tf.extractall(dest, **kw)  # noqa: S202 - our own archive; the "tar" filter blocks absolute paths and ..


def main() -> None:
    cmd, *args = sys.argv[1:]
    if cmd == "pack":
        pack(*args)
    elif cmd == "manifest":
        print(json.dumps(manifest(args[0]), indent=2))
    elif cmd == "verify":
        sys.exit(verify(args[0]))
    elif cmd == "unpack":
        unpack(*args)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
