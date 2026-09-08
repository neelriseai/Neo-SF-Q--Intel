# Generated repository knowledge

`project-index.json` gives agents a compact inventory, hashes, capability status and canonical
reading order. `application-graph.json` maps repository files through deterministic local import
and documentation-reference edges.

Regenerate after material changes:

```powershell
python scripts/catalog/build_project_index.py
```

Neither file grants authority, proves runtime execution, or replaces source and tests.
