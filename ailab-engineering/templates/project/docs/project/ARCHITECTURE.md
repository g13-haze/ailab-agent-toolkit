# Architecture — <PROJECT NAME>

Owner-approved description of how this product is structured. Keep it under ~150
lines: components, responsibilities, data flow, and the boundaries that must hold.
Agents read it during planning and review; they change it only with owner approval.

## Components
- `<path>/` — <responsibility>

## Data flow
<request -> component -> component -> store; where identity and workspace come from>

## Boundaries
The block below is checked by `intel.sh arch-check` (static imports, Python and
JS/TS). `layer <name>: <glob> ...` assigns files to a layer (first match wins);
`forbid <a> -> <b>, <c>` forbids imports from a to b and c; `only <a> -> <b>, <c>`
allows a to import only b and c (and itself). Imports of external packages are not
checked. Remove the leading `#` to activate a line.

```ailab-boundaries
# layer api: app/api/**
# layer domain: app/domain/**
# layer infra: app/db/** app/clients/**
# forbid domain -> api
# only domain -> infra
```

## Decisions
- <date> <decision> — <ADR or WP reference>
