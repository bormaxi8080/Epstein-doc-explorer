ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TMPDIR=/workspace/tmp SQLITE_TMPDIR=/workspace/tmp
COPY requirements/ ./requirements/
RUN python -m pip install --require-hashes -r requirements/build.lock -r requirements/processor.lock
COPY pyproject.toml ./
COPY ingestion/explorer_ingest/ ./ingestion/explorer_ingest/
RUN python -m pip install --no-index --no-deps --no-build-isolation .
LABEL org.opencontainers.image.title="Epstein Document Explorer Processor" org.opencontainers.image.licenses="MIT"
WORKDIR /workspace
USER 1000:1000
ENTRYPOINT ["explorer-corpus"]
CMD ["--help"]
