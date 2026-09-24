# Verifier image. Separate mode does not upload tests/, so this image owns
# the suite and sealed fixtures. filing-gate is NOT installed. Candidate
# schemas come from the submitted zip, not from this image.
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# 2.36.x requires Python <3.13; 2.37.3 is the first pin that installs on this 3.13 image.
RUN pip install --no-cache-dir \
        pytest==9.1.1 \
        pytest-json-ctrf==0.5.2 \
        arelle-release==2.37.3 \
        lxml==5.3.2

COPY test.sh test_outputs.py /tests/
COPY runner/ /tests/runner/
COPY fixtures/ /tests/fixtures/

RUN chmod 755 /tests/test.sh \
    && chmod 644 /tests/test_outputs.py \
    && chmod 644 /tests/runner/__init__.py /tests/runner/drs.py /tests/runner/package.py \
        /tests/runner/arelle_load.py /tests/runner/grade.py /tests/runner/xmlbool.py \
    && chmod 644 /tests/fixtures/* \
    && chmod 755 /tests /tests/runner /tests/fixtures \
    && test ! -e /tests/runner/mutants.py \
    && test ! -e /tests/fixtures/canonical-valid.zip \
    && test ! -d /tests/schemas

RUN mkdir -p /app/out
RUN chmod 777 /app/out
