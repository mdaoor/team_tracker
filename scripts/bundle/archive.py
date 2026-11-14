"""Pack a bundle directory into one archive (.tar.zst or .tar.gz), used by "ttctl bundle create" on the connected machine.

    archive.py pack DIR OUT.tar.zst|OUT.tar.gz

SHA256SUMS, manifest.json, VERSION and install.sh are stored first so "bundle info" can read them without unpacking
everything. zstd needs the "zstd" program or the Python package "zstandard" (uv run --with zstandard ...). Reading a
bundle (info, verify, install) needs only tar, gzip or zstd and sha256sum - no Python on the offline host.
"""

import os
import shutil
import subprocess
import sys
import tarfile

FIRST = ("SHA256SUMS", "SHA256SUMS.asc", "manifest.json", "VERSION", "install.sh")


def _members(src: str) -> list[str]:
    names = []
    for root, dirs, files in os.walk(src):
        dirs.sort()
        names += [os.path.relpath(os.path.join(root, fn), src) for fn in sorted(files)]
    return sorted(names, key=lambda n: (n not in FIRST, n))


def pack(src: str, out: str) -> None:
    top = os.path.basename(os.path.normpath(src))
    names = _members(src)

    def add_all(tf: tarfile.TarFile) -> None:
        for rel in names:
            tf.add(os.path.join(src, rel), arcname=f"{top}/{rel}", recursive=False)

    if out.endswith((".gz", ".tgz")):
        with tarfile.open(out, "w:gz", compresslevel=6) as tf:
            add_all(tf)
        return
    exe = shutil.which("zstd")
    if exe:
        with open(out, "wb") as f:
            p = subprocess.Popen([exe, "-q", "-T0", "-10", "-c"], stdin=subprocess.PIPE, stdout=f)  # noqa: S603
            if p.stdin is None:
                raise RuntimeError("zstd did not start")
            with tarfile.open(fileobj=p.stdin, mode="w|") as tf:
                add_all(tf)
            p.stdin.close()
            if p.wait() != 0:
                sys.exit("zstd failed")
        return
    import zstandard  # type: ignore[import-not-found,unused-ignore]

    with open(out, "wb") as f, zstandard.ZstdCompressor(level=10, threads=-1).stream_writer(f) as w:
        with tarfile.open(fileobj=w, mode="w|") as tf:
            add_all(tf)


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] != "pack":
        sys.exit(__doc__)
    pack(sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()
