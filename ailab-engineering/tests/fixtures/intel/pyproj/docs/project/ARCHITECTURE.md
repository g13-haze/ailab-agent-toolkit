# Architecture — shop (fixture)

The API layer calls the domain; the domain uses utilities; utilities depend on nothing internal.
Money formatting lives in `src/shop/util/money.py`. The old pricing engine `src/shop/old_engine.py` was removed.

```ailab-boundaries
layer api: src/shop/api/**
layer util: src/shop/util/**
layer domain: src/shop/*.py
forbid util -> domain, api
only domain -> util
```
