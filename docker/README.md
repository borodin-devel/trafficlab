# Docker assets

Trafficlab uses Docker Compose for workload isolation and a single checked image
context for packet capture. The Python process creates the Compose project,
starts the capture and target containers, enforces the configured deadlines,
and removes project-scoped containers, networks, volumes, and orphans.

## Directory layout

| Path | Purpose |
| --- | --- |
| `capture/Dockerfile` | Builds the reproducible capture image from a digest-pinned Debian snapshot. |
| `capture/capture.sh` | Validates `eth0`, writes capture metadata atomically, runs `dumpcap`, and orders the resulting PCAPNG with `reordercap`. |
| `capture/image-lock.json` | Records the source image, package, tool, and expected local image identities used by preflight. |

The capture context is infrastructure for the `trafficlab capture` and
`trafficlab run` commands. It is not intended to be started by itself: the
orchestrator supplies the network, output mount, capabilities, deadlines, and
cleanup ownership described in
[`architecture/CAPTURE.md`](../architecture/CAPTURE.md).

## Build

Build from the repository root so the context and tag agree with the example
configuration:

```bash
docker build --pull --no-cache \
  --tag trafficlab-capture:local docker/capture
```

For reproducibility evidence, also use `--iidfile` and compare the resulting
image ID with `expected_capture_image_id`. The Validation Study performs that
cold build and retains the exact command and result; see
[`examples/validation_study/README.md`](../examples/validation_study/README.md).

## `image-lock.json` fields

| Field | Description |
| --- | --- |
| `base_reference` | Human-readable Debian image tag corresponding to the pinned base. |
| `base_digest` | Immutable SHA-256 digest used in the Dockerfile `FROM` instruction. |
| `debian_snapshot` | UTC Debian Snapshot archive timestamp used for deterministic package retrieval. |
| `direct_packages` | Map from each directly installed Debian package to its exact version. |
| `capture_tool_version` | Expected `dumpcap`/Wireshark version in the built image. |
| `expected_capture_image_id` | SHA-256 ID of the accepted cold-built local capture image. |

The lock is checked input. Update it only with the Dockerfile and the associated
cold-build and integration evidence.

## Generated `capture.json` fields

The capture entrypoint writes `/trafficlab/capture.json` before capture begins.
The same two-field document appears in run and example artifact directories.

| Field | Description |
| --- | --- |
| `interface` | Interface captured inside the container; currently exactly `eth0`. |
| `target_mac` | Lowercase unicast MAC address used to classify frames as inbound or outbound. |

On termination, `capture.sh` asks `dumpcap` to flush, orders packets by timestamp,
and leaves the temporary capture for the Python lifecycle owner to validate and
publish atomically. Failures remain failures; the script does not fabricate an
empty or partial successful artifact.
