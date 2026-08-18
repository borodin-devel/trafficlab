FROM python:3.12.11-slim-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0555 client.py /opt/trafficlab/client.py

ENTRYPOINT ["python", "/opt/trafficlab/client.py"]
