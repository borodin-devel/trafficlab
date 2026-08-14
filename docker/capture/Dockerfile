FROM debian:bookworm-20260803-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wireshark-common \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 capture.sh /usr/local/bin/trafficlab-capture

ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]
