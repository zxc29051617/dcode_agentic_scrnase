# FastQ + Count matrix main graph

這版只保留兩個主入口：

- **FASTQ**
- **Count matrix**

Count matrix 再拆成：

- **raw matrix**：可能還沒做 cell calling
- **filtered matrix**：通常已經做過 cell calling

```mermaid
flowchart TD
    A[Input bundle] --> B[Ingest / preflight / intake validation]
    B --> C{Main input type}

    C -->|FASTQ bundle| F0[Upstream preprocessing]
    C -->|Count matrix bundle| M0[Count matrix classifier]

    %% FASTQ route
    F0 --> F1[nfcore-scrnaseq-wrapper]
    F1 --> F2[Count matrix produced]
    F2 --> M0

    %% Count matrix route
    M0 -->|raw_feature_bc_matrix / raw-count h5ad| R0[Raw-count route]
    M0 -->|filtered_feature_bc_matrix / filtered-count h5ad| P0[Filtered-count route]

    R0 --> R1[Validate raw counts + source state]
    R1 --> R2{Cell calling already resolved?}
    R2 -->|no| R3[Cell calling review]
    R2 -->|yes| D0[Downstream Scanpy mainline]

    R3 --> J3[Judge cell calling]
    J3 -->|pass| D0
    J3 -->|warn| H1[Human review]
    J3 -->|fail| H1

    P0 --> P1[Validate filtered counts]
    P1 --> D0

    %% Main Scanpy line
    D0 --> S1[QC metrics / raw load]
    S1 --> J1[Judge QC]
    J1 -->|pass| S2[Cell QC filter]
    J1 -->|warn| H1
    J1 -->|fail| H1

    S2 --> J2[Judge cell QC filter]
    J2 -->|pass| S3[Doublet detection]
    J2 -->|warn| H1
    J2 -->|fail| H1

    S3 --> J4[Judge doublets]
    J4 -->|pass| S4[Normalize / HVG / PCA prep]
    J4 -->|warn| H1
    J4 -->|fail| H1

    S4 --> J5[Judge preprocessing]
    J5 -->|pass| S5[PCA]
    J5 -->|warn| H1
    J5 -->|fail| H1

    S5 --> J6[Judge PCA]
    J6 -->|pass| S6[Integration]
    J6 -->|warn| H1
    J6 -->|fail| H1

    S6 --> J7[Judge integration]
    J7 -->|pass| S7[Clustering]
    J7 -->|warn| H1
    J7 -->|fail| H1

    S7 --> J8[Judge clustering]
    J8 -->|pass| S8[UMAP]
    J8 -->|warn| H1
    J8 -->|fail| H1

    S8 --> S9[Markers]
    S9 --> J9[Judge markers]
    J9 -->|pass| S10[Annotation]
    J9 -->|warn| H2[Human annotation review]
    J9 -->|fail| H2

    S10 --> J10[Judge annotation]
    J10 -->|pass| H2
    J10 -->|warn| H2
    J10 -->|fail| H2

    H2 --> RPT[Finalize decisions + report]
    H1 --> RPT2[Revision / stop / reroute]

    %% Cross-cutting infrastructure
    subgraph X[Cross-cutting infrastructure]
        X1[State store / checkpoint]
        X2[Audit log / provenance]
        X3[Artifact registry]
        X4[Local model endpoint]
    end

    B -.-> X1
    B -.-> X2
    C -.-> X3
    J1 -.-> X4
    J2 -.-> X4
    J3 -.-> X4
    J4 -.-> X4
    J5 -.-> X4
    J6 -.-> X4
    J7 -.-> X4
    J8 -.-> X4
    J9 -.-> X4
    J10 -.-> X4

    RPT --> Z[HTML / PDF report]
    RPT2 --> Z
```

## 這版的意思

- **主入口只有 FASTQ 和 Count matrix。**
- Count matrix 不再和 h5ad / latent data 混在一起。
- raw matrix 會先看 cell calling 是否已經解決；沒有的話就進 cell calling review。
- filtered matrix 直接進 downstream Scanpy 主線。
- 每個重要 step 後面都有 local judge；`warn` / `fail` 會把流程拉回 human review。

## 為什麼這樣最適合 MVP

1. 你現在的主要資料型態就是 **FASTQ** 與 **Count matrix**。
2. Count matrix 的真實差異不是檔名，而是 **raw vs filtered**。
3. 這樣可以先把 workflow 做穩，不需要一開始就支援所有 h5ad / latent 路徑。
4. 後面要擴充成 integrated h5ad / X_scvi 時，可以再加一條 side route，不影響主線。

## 對應 ClawBio

- `nfcore-scrnaseq-wrapper`：FASTQ → count matrix / preferred_h5ad
- `scrna-orchestrator`：count matrix → QC, clustering, markers, annotation
