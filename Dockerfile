FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src HOME=/home/jobradar PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.lock.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.lock.txt \
    && python -m pip check \
    && mkdir -p /home/jobradar/.claude-mem
# The isolated PDF extractor samples the worker's RSS via /bin/ps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends procps \
    && apt-get clean \
    && find /var/lib/apt/lists -mindepth 1 -delete
COPY src/job_radar/ ./src/job_radar/

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "job_radar.api_v1_serve"]
