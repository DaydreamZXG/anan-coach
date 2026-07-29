# Post-repair project-map delta

This file is an additive snapshot relative to the immutable audit baseline. It
does not rewrite `page-inventory.csv`, `coverage-matrix.csv` or
`api-inventory.csv`, which describe commit
`01752f1d7b8b606b6900160eebba0888d8def294`.

Final integration commit:
`3f7b2e703098b9fbb053f9838929633c5a03ed0b`.

## Counts

| Inventory | Audit baseline | Final integration | Delta |
|---|---:|---:|---:|
| Registered routes | 64 | 65 | +1 |
| Business pages | 58 | 59 | +1 |
| Asset entries (business coverage N/A) | 6 | 6 | 0 |
| Visual scenarios | 88 | 95 | +7 |
| Business-page interaction contracts | 58 | 59 | +1 |
| OpenAPI paths | 233 | 235 | +2 |
| OpenAPI operations | 244 | 247 | +3 |

The final OpenAPI count was exported read-only to
`audit/repair-evidence/openapi-final-3f7b2e703.json`.

## Added page

| Page | Route | Package | Domain | Template | Entry | Route parameters | API | Login | Post-repair render |
|---|---|---|---|---|---|---|---|---|---|
| 收藏题目 | `/pages/xingce-mastery/favorites/index` | Main | 行测掌握 / 长期收藏 | Account-scoped stateful list | `/pages/xingce-mastery/index` | None | List favorites; cancel favorite | Required | Pending five-profile final capture |

The page supports current-version, updated-content and safe tombstone rows. It
offers preview and cancellation only; it does not create an unplanned practice
entry.

## Added API operations

All three operations require the authenticated account:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v2/xingce/question-favorites` | Real-time keyset favorite list |
| `GET` | `/api/v2/xingce/question-favorites/{question_id}` | Current account-scoped favorite state |
| `PUT` | `/api/v2/xingce/question-favorites/{question_id}` | Desired-state favorite command with `Idempotency-Key` |

## Added visual scenarios

| Scenario | Route | State |
|---|---|---|
| `c16-xingce-shared-material-actions` | practice | Shared-material action hierarchy |
| `c17-xingce-single-favorited-marked` | practice | Favorite and mark coexist independently |
| `c18-xingce-more-tools` | practice | Low-frequency tools panel |
| `c19-xingce-favorites-list` | favorites | Current, updated and tombstone rows |
| `c20-xingce-favorites-empty` | favorites | Empty state |
| `c21-xingce-favorites-error` | favorites | Error and retry state |
| `h17-capture-camera-init-error` | capture import | Camera initialization error |

No baseline visual scenario was removed.

## Evidence boundary

The count and contract changes are verified by the final WeChat and backend
automated gates. The `Post-repair render` field remains pending until the five
final manifests are complete. This map delta does not claim production,
cross-device or real-account E2E verification.
