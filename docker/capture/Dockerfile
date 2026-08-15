ARG SOURCE_DATE_EPOCH=1785789333
FROM debian:bookworm-20260803-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241

RUN printf '%s\n' \
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20260803T203533Z/ bookworm main' \
    'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20260803T203533Z/ bookworm-security main' \
    > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
   ca-certificates=20230311+deb12u1 \
   curl=7.88.1-10+deb12u15 \
   wireshark-common=4.0.17-0+deb12u3 \
 && rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/* /tmp/* /var/tmp/*

COPY --chmod=0755 capture.sh /usr/local/bin/trafficlab-capture

ENTRYPOINT ["/usr/local/bin/trafficlab-capture"]
