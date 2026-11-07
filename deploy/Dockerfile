FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

ARG DEBIAN_MIRROR=http://deb.debian.org
ARG PYTHON_INDEX=https://pypi.org/simple
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY shilun ./shilun
ARG OFFLINE_WHEELS=0
COPY deploy/wheels/ /wheels/
RUN if [ "$OFFLINE_WHEELS" = "1" ]; then \
      python -m pip install --no-index --find-links=/wheels setuptools wheel \
      && python -m pip install --no-index --find-links=/wheels --no-build-isolation .; \
    else python -m pip install --no-cache-dir --index-url "$PYTHON_INDEX" .; fi

RUN groupadd --system shilun \
    && useradd --system --gid shilun --home-dir /app shilun \
    && mkdir -p /app/outputs /app/runtime /app/logs \
    && chown -R shilun:shilun /app

USER shilun

EXPOSE 8000

CMD ["python", "-m", "shilun"]
