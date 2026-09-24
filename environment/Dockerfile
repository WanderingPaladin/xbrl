# Agent image. Nothing from tests/ or solution/ is copied here.
# filing-gate is agent-only; the verifier image does not include it.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asciinema \
        ca-certificates \
        libxml2 \
        libxslt1.1 \
        tmux \
    && rm -rf /var/lib/apt/lists/*

# 2.36.x requires Python <3.13; 2.37.3 is the first pin that installs on this 3.13 image.
RUN pip install --no-cache-dir \
        arelle-release==2.37.3 \
        lxml==5.3.2

WORKDIR /app

COPY taxonomy/prior/ /app/prior-package/taxonomy/
COPY package-meta/prior/ /app/prior-package/META-INF/
COPY taxonomy/current/ /app/current-package/taxonomy/
COPY package-meta/current/ /app/current-package/META-INF/
COPY desk/ /app/desk/
COPY bin/filing-gate /usr/local/bin/filing-gate

RUN chmod 755 /usr/local/bin/filing-gate
RUN mkdir -p /app/out
RUN chmod 777 /app/out
