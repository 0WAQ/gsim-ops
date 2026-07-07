---
name: factor-library-system-design-reference
description: "Open-source projects to study for understanding factor library management system design — Feast, Dagster, DVC, and related projects covering data versioning, pipeline orchestration, and feature registry"
metadata: 
  node_type: memory
  type: reference
  originSessionId: cdfffb9f-d84e-45a3-9001-539b85c57e60
---

## Core Three to Study

### 1. Feast — Feature Registry & Lifecycle
- Website: https://feast.dev
- GitHub: https://github.com/feast-dev/feast
- Why: Closest open-source analogue to a "factor library management system"
- Core abstractions to study:
  - `FeatureView` — logical group of features (≈ one factor's daily alpha values across dates/instruments)
  - `FeatureService` — user-facing bundle of features (≈ a QR's factor set)
  - `Registry` — centralized metadata store for all feature definitions
  - `OfflineStore` — historical feature data for batch retrieval/backtesting
  - `OnlineStore` — low-latency feature serving for live trading
- Maps to gsim-ops: Registry ≈ factor_state.json + meta.json, OfflineStore ≈ alpha_feature, FeatureView ≈ a factor

### 2. Dagster — Data Pipeline Orchestration
- Website: https://dagster.io
- GitHub: https://github.com/dagster-io/dagster
- Why: "Software-defined asset" model matches how factors flow through validation → production
- Core abstractions to study:
  - `Asset` — a data product with defined dependencies (≈ one stage's output in a factor pipeline)
  - `Op` — a single computation step (≈ one checker in the check pipeline)
  - `Job` — a scheduled/triggered execution of a graph of ops
  - `AssetMaterialization` — recorded event when an asset is produced
  - `Sensor` / `Schedule` — triggers for pipeline execution
- Maps to gsim-ops: check.py's 8-stage pipeline ≈ a Dagster job, reconcile ≈ asset reconciliation

### 3. DVC — Data Versioning
- Website: https://dvc.org
- GitHub: https://github.com/iterative/dvc
- Why: Manages large files with Git-like semantics — metafiles track content hashes + remote storage paths
- Core abstractions to study:
  - `.dvc` metafile — pointer file recording md5 + remote path (≈ meta.json)
  - `dvc push/pull` — sync data to/from remote storage
  - Data registry — tracks which versions of data were used in which experiments
- Maps to gsim-ops: meta.json ≈ .dvc file, sync push/pull ≈ dvc push/pull

## Supplementary References

### Data Storage & Formats
- **Zarr** (https://zarr.dev) — chunked, compressed, parallel N-dimensional array storage. Direct alternative to the 1.8M-individual-npy-files problem. Supports incremental writes along any axis.
- **Apache Arrow** (https://arrow.apache.org) — columnar memory format, zero-copy IPC between computation modules. The data-passing layer gsim's NIO wrapper approximates.
- **Apache Parquet** (https://parquet.apache.org) — columnar file format with page-level checksums and row-group statistics. Reference for data integrity/verification patterns.
- **xarray** (https://xarray.dev) — labeled multi-dimensional arrays (date × instrument × feature). Would replace manual di/ii index bookkeeping in pack.py.

### Pipeline & Orchestration
- **Prefect** (https://prefect.io) — lighter-weight Python-native workflow engine. Alternative to Dagster; simpler mental model.
- **Temporal** (https://temporal.io) — workflow-as-code with strong consistency guarantees. Reference for state-machine-based lifecycle management.
- **Celery** — distributed task queue. Reference for task state model (PENDING → RECEIVED → STARTED → SUCCESS/FAILURE/RETRY).

### Feature/Model Registry
- **MLflow Model Registry** (https://mlflow.org) — model versioning + stage transitions (Staging/Production/Archived). Stage model maps directly to factor status (SUBMITTED/ACTIVE/REJECTED).

### General System Design
- **kubectl** / **gh** CLI design — declarative subcommand trees for user-facing interfaces
- **Click** / **Typer** — Python CLI framework design patterns
- **Pydantic** — type-safe configuration and data validation at system boundaries

## Learning Strategy

1. Read architecture docs and concept pages first — understand WHY the system is designed that way
2. Run a quickstart — feel the API from the user's perspective
3. Map each concept back to gsim-ops — "this is like my X module, but done properly"
4. Deeper dives per module as needed — no need to read full source upfront
