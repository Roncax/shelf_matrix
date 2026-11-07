FROM python:3.11-slim

LABEL org.opencontainers.image.source=https://github.com/Roncax/shelf-matrix

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SHELF_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY shelf_matrix ./shelf_matrix
RUN pip install '.[server]'

RUN useradd --system --uid 10001 --no-create-home shelf \
    && mkdir /data && chown shelf:shelf /data
USER shelf
VOLUME /data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
CMD ["python", "-m", "shelf_matrix.server"]
