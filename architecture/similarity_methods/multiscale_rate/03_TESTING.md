# 03 Testing and Reading

## Testing

Future deterministic unit tests cover bin boundaries, time-zero bins,
zero-count bins, post-horizon exclusion diagnostics, packet and original-byte
vectors, normalized-L1 bounds and exact distances, feature and scale weights,
invalid and insufficient configuration, empty captures, and raw diagnostics.
Future integration tests use small offline Ethernet PCAPNG fixtures and verify
`similarity.toml` details. No test requires network access, live capture,
sudo, root, or other elevated privilege.

## Reading

Follow the [architecture governance](../../README.md), the
[similarity-method registry](../README.md), the shared [temporal Layer 2
rules](../TEMPORAL_L2.md), the [60 similarity evaluation
application](../../apps/60_similarity_evaluation/README.md), and the
[similarity result contract](../../contracts/60_30_similarity_result/README.md)
before changing this method.
