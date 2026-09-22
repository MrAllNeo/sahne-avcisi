FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir .

ENV SAHNE_HOST=0.0.0.0 \
    SAHNE_DATA_DIR=/data \
    SAHNE_SOURCES_FILE=/app/config/sources.json

VOLUME ["/data"]
EXPOSE 8080
# Railway (and similar PaaS) inject a runtime PORT env var and route to it;
# a fixed ENV SAHNE_PORT would ignore that. Resolve it at container start,
# falling back to 8080 for plain `docker run` without PORT set.
CMD ["sh", "-c", "SAHNE_PORT=${PORT:-8080} exec sahne-avcisi"]
