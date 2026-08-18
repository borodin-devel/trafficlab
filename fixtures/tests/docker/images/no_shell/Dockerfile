# syntax=docker/dockerfile:1
FROM alpine:3.22.1 AS build

RUN apk add --no-cache build-base
RUN <<'EOF'
cat > /tmp/workload.c <<'SOURCE'
#include <errno.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <string.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv) {
    const char payload[] = "trafficlab-no-shell";
    struct sockaddr_in endpoint = {0};
    char *end = 0;
    long status;
    int connection;
    if (argc != 2) return 64;
    errno = 0;
    status = strtol(argv[1], &end, 10);
    if (errno != 0 || *end != '\0' || status < 0 || status > 255) return 64;
    connection = socket(AF_INET, SOCK_STREAM, 0);
    if (connection < 0) return 70;
    endpoint.sin_family = AF_INET;
    endpoint.sin_port = htons(18080);
    if (inet_pton(AF_INET, "172.31.254.2", &endpoint.sin_addr) != 1) return 70;
    if (connect(connection, (struct sockaddr *)&endpoint, sizeof(endpoint)) != 0) return 70;
    if (write(connection, payload, strlen(payload)) < 0) return 70;
    close(connection);
    return (int)status;
}
SOURCE
EOF
RUN cc -static -Os -s -o /workload /tmp/workload.c

FROM scratch

COPY --from=build /workload /workload

ENTRYPOINT ["/workload"]
