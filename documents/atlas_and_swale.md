# Atlas and Swale — Architecture Diagram

The **Atlas** is the full system: configuration, inlets, eddies, outlets, and the dataswale. The **Dataswale** is only the storage component — a standardized internal representation of layers. Swapping storage backends (GeoJSON files → S3 → PostGIS) should require no changes to the materialization layer above it.

```mermaid
graph TD
    subgraph Atlas
        CFG[Configuration\nassets, layers, bbox, URLs]

        subgraph Inlets
            VI[Vector Inlets\nOSM · Overture · local files]
            RI[Raster Inlets\nOpenTopo · local GeoTIFF]
        end

        subgraph Eddies
            ED[Transforms\ncontours · hillshade · H3]
        end

        subgraph Dataswale
            direction TB
            LY[Layers\nstandardized internal format]
            DL[Deltas\nappend · annotate · delete]
            VR[Versions\nstaging · published snapshots]
            DL --> LY
            LY --> VR
        end

        subgraph Outlets
            WM[Webmap]
            RB[Runbook PDF]
            GZ[Gazetteer]
            NB[Notebook]
            HTML[HTML Console]
        end

        EXT[(External Sources\nOSM · Overture · OpenTopo\nlocal files · email · sheets)]

        EXT -->|fetch + pre-process| VI
        EXT -->|fetch + pre-process| RI
        VI -->|materialize| DL
        RI -->|materialize| LY
        ED -->|transform| LY
        LY --> WM
        LY --> RB
        LY --> GZ
        LY --> NB
        LY --> HTML
        CFG -.->|configures| Inlets
        CFG -.->|configures| Eddies
        CFG -.->|configures| Outlets
        CFG -.->|configures| Dataswale
    end
```

## Key Distinction

`atlas.materialize()` — Atlas-level. Fetches from an external source, pre-processes, and produces data in a form ready to enter the dataswale. **Does not care how the dataswale stores things.**

`dataswale.refresh_vector_layer()` — Dataswale-level. Applies deltas and writes the canonical layer file. **Does not care where the data came from.**

This separation means:
- A new inlet (e.g. email photo) plugs into the Atlas layer without touching storage
- A new storage backend (e.g. S3, PostGIS) plugs into the Dataswale without touching inlets or outlets
- Eddies sit between: they read from and write to the Dataswale, but are configured and invoked by the Atlas
