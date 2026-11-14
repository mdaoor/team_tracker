# The host from the motivating report: RHEL 8 family, Python 3.13 (uv-managed), uv, Node 22, git, curl 7.61, psql 10.23, NO pnpm.
# Used by ci/ops/repro-rhel8.sh. When the Rocky mirrors are unreachable (some sandboxes), git is skipped and psql is a stub
# that answers like PostgreSQL 10.23 - the only thing the scripts read from it is the version line.
FROM rockylinux:8
ARG NODE_VERSION=22.11.0
# extra CA (an intercepting proxy in some build environments); the directory is empty elsewhere
COPY ca/ /etc/pki/ca-trust/source/anchors/
RUN update-ca-trust
ENV SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
RUN (dnf -y -q install git postgresql xz tar >/dev/null 2>&1 && echo "real psql: $(psql --version)") || \
    (printf '#!/bin/sh\necho "psql (PostgreSQL) 10.23"\n' > /usr/local/bin/psql && chmod +x /usr/local/bin/psql && echo "stub psql")
# uv from PyPI (works where github.com is blocked), then Python 3.13 managed by uv
RUN set -e; cd /tmp; \
    url=$(curl -fsS https://pypi.org/simple/uv/ -H 'Accept: application/vnd.pypi.simple.v1+json' | \
          /usr/libexec/platform-python -c 'import json,sys;fs=[f for f in json.load(sys.stdin)["files"] if "manylinux" in f["filename"] and "x86_64" in f["filename"] and not f.get("yanked")];print(fs[-1]["url"])'); \
    curl -fsSL -o uv.whl "$url"; \
    /usr/libexec/platform-python -c 'import zipfile;z=zipfile.ZipFile("uv.whl");[open("/usr/local/bin/"+n.split("/")[-1],"wb").write(z.read(n)) for n in z.namelist() if n.endswith("/scripts/uv") or n.endswith("/scripts/uvx")]'; \
    chmod +x /usr/local/bin/uv /usr/local/bin/uvx; rm uv.whl; \
    UV_PYTHON_INSTALL_DIR=/opt/python uv python install 3.13; ln -s "$(UV_PYTHON_INSTALL_DIR=/opt/python uv python find 3.13)" /usr/local/bin/python3.13; \
    ln -sf /usr/local/bin/python3.13 /usr/local/bin/python3
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz | tar -xz -C /opt && \
    ln -s /opt/node-v${NODE_VERSION}-linux-x64/bin/node /usr/local/bin/node && ln -s /opt/node-v${NODE_VERSION}-linux-x64/bin/npm /usr/local/bin/npm && \
    rm -f /opt/node-v${NODE_VERSION}-linux-x64/bin/corepack
ENV UV_PYTHON_INSTALL_DIR=/opt/python
WORKDIR /src
