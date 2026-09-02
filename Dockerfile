ARG BASE_IMAGE=docker.m.daocloud.io/library/python:3.14-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    PYTHONPATH=/app

COPY docker/debian.sources /etc/apt/sources.list.d/debian.sources
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -c "import tables"

COPY vendor/ /opt/vendor/
RUN test -n "$(find /opt/vendor -maxdepth 1 -name 'tgw-*.whl' -print -quit)" \
    && test -n "$(find /opt/vendor -maxdepth 1 -name 'AmazingData-*.whl' -print -quit)" \
    && python -m pip install --no-cache-dir /opt/vendor/tgw-*.whl /opt/vendor/AmazingData-*.whl \
    && rm -rf /opt/vendor

COPY gateway/ /app/gateway/
WORKDIR /app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/health/live || exit 1

CMD ["python", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8765"]
