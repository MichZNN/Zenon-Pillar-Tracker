FROM python:3.14.5-slim-bookworm

ARG BUILD_VERSION=unknown

LABEL org.opencontainers.image.title="Zenon Pillar Tracker" \
      org.opencontainers.image.description="Zenon Network pillar collector and dashboard" \
      org.opencontainers.image.version="${BUILD_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt

COPY . /app

# The application only needs to write its mounted data directory. Keeping the
# process unprivileged also makes the image suitable for rootful and rootless
# Docker installations where the mapped data directory is prepared by the
# operator.
RUN addgroup --system --gid 10001 tracker \
    && adduser --system --uid 10001 --ingroup tracker --no-create-home tracker \
    && mkdir -p /app/data_store \
    && chown -R tracker:tracker /app/data_store

USER tracker

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5).read()"]

CMD ["python", "web_app.py", "--host", "0.0.0.0", "--port", "8080", "--database", "/app/data_store/pillar_tracker.sqlite3"]
