FROM python:3.12.11-slim-bookworm

COPY --chmod=0555 server.py /opt/trafficlab/server.py

HEALTHCHECK --interval=200ms --timeout=1s --start-period=1s --retries=30 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 18080), 1).close()"]

ENTRYPOINT ["python", "/opt/trafficlab/server.py"]
