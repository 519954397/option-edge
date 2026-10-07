FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY jin10_research ./jin10_research
RUN python -m pip install --no-cache-dir .

USER app
EXPOSE 8790

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8790/live', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "jin10_research"]
CMD ["option-radar", "--host", "0.0.0.0", "--port", "8790", "--refresh", "10"]
