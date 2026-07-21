# File Contracts

## Role

Contracts define versioned producer-consumer file interfaces, semantic
validation, hashes, lineage, and publication completeness.

## Components

- [Capture request](00_capture_request/README.md)
- [Capture readiness](00_10_capture_readiness/README.md)
- [Capture directions](10_20_capture_directions/README.md)
- [Inspection dataset](25_inspection_dataset/README.md)
- [Similarity result](60_30_similarity_result/README.md)

Contract implementations belong in shared validators and producing/consuming
applications. A consumer never infers an undocumented field or artifact.
