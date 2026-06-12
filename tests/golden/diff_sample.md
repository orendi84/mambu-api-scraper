# Mambu API Structural Diff

Old: 2026-01-01T00:00:00+00:00 (tenant demo.example.com)
New: 2026-02-01T00:00:00+00:00 (tenant demo.example.com)

## Summary

| Category | Added | Removed | Changed |
|----------|-------|---------|---------|
| Resources | 1 | 1 | 1 |
| Endpoints | 1 | 1 | 1 |
| Schemas | 0 | 0 | 1 |

Deprecations added: 0 | Deprecations removed: 0

## Resources added

- Loans

## Resources removed

- Legacy

## Clients

- Added: `POST /clients`
- Removed: `DELETE /clients/{id}`
- Changed: `GET /clients`
  - Parameter `limit` (in query) type: string -> integer
- Schema changed: `Client`
  - Property added: `name`
