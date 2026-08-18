# Test fixtures

Shared fixture helpers live beside this file. Immutable test-only bytes are
grouped by domain under `data/`; tests access them through `paths.py` rather than
embedding repository-relative paths throughout the suite.
