"""Make the installed Team Tracker packages run from the release's source tree (what an editable install does).

The wheels give the console scripts (tt-admin, tt, tt-llm, ...) and package metadata; the server, however, locates
alembic.ini, its migrations and tools/seed relative to its source directory. Building a real editable install needs a
build backend, which an offline host does not have, so this removes the copied package directories from site-packages
and adds one .pth file that puts the source directories on sys.path instead.

    .venv/bin/python scripts/bundle/link_sources.py RELEASE_DIR
"""

import os
import shutil
import site
import sys

rel = os.path.abspath(sys.argv[1])
roots = [os.path.join(rel, "apps", d) for d in sorted(os.listdir(os.path.join(rel, "apps")))]
roots += [os.path.join(rel, "packages", d, "src") for d in sorted(os.listdir(os.path.join(rel, "packages")))]
sp = site.getsitepackages()[0]
linked = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for name in sorted(os.listdir(root)):
        src = os.path.join(root, name)
        if name.startswith("tt_") and os.path.isfile(os.path.join(src, "__init__.py")):
            installed = os.path.join(sp, name)
            if os.path.isdir(installed):
                shutil.rmtree(installed)
            linked.append(root)
            break
with open(os.path.join(sp, "_team_tracker_sources.pth"), "w") as f:
    f.write("\n".join(dict.fromkeys(linked)) + "\n")
print(f"linked {len(linked)} source directories into {sp}")
