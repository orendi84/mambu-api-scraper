# Mambu API Quality Scorecard

## Overall

| Metric | Value |
|--------|-------|
| Composite | 50.0 |
| Resources | 2 |
| Operations | 3 |
| Described % | 33.3 |
| 2xx schema % | 33.3 |
| Examples % | 33.3 |
| Params described % | 100.0 |
| Deprecation hygiene | 66.7 |
| Deprecated ops | 1 |

## Per resource

Sorted worst composite first.

| Resource | Composite | Ops | Described % | 2xx schema % | Examples % | Params described % | CRUD | Deprecated |
|----------|-----------|-----|-------------|--------------|------------|--------------------|------|------------|
| Loans | 0.0 | 1 | 0.0 | 0.0 | 0.0 | n/a | ----D | 1 |
| Clients | 65.0 | 2 | 50.0 | 50.0 | 50.0 | 100.0 | L-C-- | 0 |

## Metric definitions and weights

- Described (weight 25): operations with a non-empty summary or description, over all operations.
- 2xx schema (weight 25): among operations with at least one 2xx or 102 response, those where at least one such response has a content schema.
- Examples (weight 20): operations carrying any example (parameter, requestBody content, or response content), over all operations.
- Params described (weight 20): resolved parameters with a non-empty description, over all parameters; resources with zero parameters are excluded (n/a) and renormalized.
- Deprecation hygiene (weight 10): 100 if no deprecated operations, otherwise 100 times (1 minus deprecated divided by total operations).
- Composite: weighted mean of the metrics above, 0 to 100; n/a metrics are dropped and remaining weights renormalized. CRUD is reported as flags (L list, R item read, C create, U update, D delete) and is not part of the composite. All values rounded half-up to 1 decimal.
