# Build an Output

Outputs — the webmap, PDF runbook, gazetteer map book, GeoPackage, and so on —
are generated from your current staging data. **Publishing does not rebuild
them**, so if you've changed data and want an output to reflect it, build that
output first.

## Building from the Admin Console

Each entry in the **Maps** and **Downloads** sections has a **Build** button.
Click it to regenerate that output from current staging data; a status message
reports when it finishes.

- Lightweight outputs like the **webmap** are also kept current automatically
  when you [refresh a layer](refresh_layer.md).
- Heavier outputs like the **PDF runbook** and **gazetteer** are only built when
  you ask. Build these explicitly before publishing if their data has changed.

## Before publishing

A good habit before [publishing a version](publish_version.md): build any
outputs whose data has changed, confirm they look right in staging, then publish.
Publishing takes an exact snapshot of staging — whatever an output shows at
publish time is what gets frozen into the version.
