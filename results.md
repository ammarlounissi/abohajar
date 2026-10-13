# تقرير فحص API: http://127.0.0.1:8000

ملخص: `{'PASS': 69, 'SKIP': 15, 'FAIL': 1}`

| النتيجة | الطريقة | المسار | الفحص | الحالة | الثواني |
|---|---|---|---|---:|---:|
| PASS | GET | `/openapi.json` | openapi | 200 | 0.038 |
| PASS | POST | `/api/factories/` | missing-api-key | 401 | 0.002 |
| SKIP | POST | `/api/factories/` | write-not-executed | - | 0 |
| PASS | GET | `/api/factories/` | missing-api-key | 401 | 0.002 |
| PASS | GET | `/api/factories/` | read-route | 200 | 0.003 |
| PASS | GET | `/api/factories/{factory_id}` | missing-api-key | 401 | 0.004 |
| PASS | GET | `/api/factories/{factory_id}` | read-route | 200 | 0.004 |
| PASS | PATCH | `/api/factories/{factory_id}` | missing-api-key | 401 | 0.002 |
| SKIP | PATCH | `/api/factories/{factory_id}` | write-not-executed | - | 0 |
| PASS | DELETE | `/api/factories/{factory_id}` | missing-api-key | 401 | 0.002 |
| SKIP | DELETE | `/api/factories/{factory_id}` | write-not-executed | - | 0 |
| PASS | POST | `/api/posts/` | missing-api-key | 401 | 0.002 |
| SKIP | POST | `/api/posts/` | write-not-executed | - | 0 |
| PASS | GET | `/api/posts/` | missing-api-key | 401 | 0.005 |
| PASS | GET | `/api/posts/` | read-route | 200 | 0.009 |
| PASS | GET | `/api/posts/{post_id}` | missing-api-key | 401 | 0.005 |
| PASS | GET | `/api/posts/{post_id}` | read-route | 200 | 0.011 |
| PASS | PATCH | `/api/posts/{post_id}` | missing-api-key | 401 | 0.009 |
| SKIP | PATCH | `/api/posts/{post_id}` | write-not-executed | - | 0 |
| PASS | DELETE | `/api/posts/{post_id}` | missing-api-key | 401 | 0.004 |
| SKIP | DELETE | `/api/posts/{post_id}` | write-not-executed | - | 0 |
| PASS | POST | `/api/products/` | missing-api-key | 401 | 0.004 |
| SKIP | POST | `/api/products/` | write-not-executed | - | 0 |
| PASS | GET | `/api/products/` | read-route | 200 | 0.026 |
| PASS | GET | `/api/products/` | public-response-no-sensitive-fields | 200 | 0.022 |
| PASS | GET | `/api/products/` | limit-over-max | 422 | 0.005 |
| PASS | GET | `/api/products/` | negative-skip | 422 | 0.006 |
| PASS | GET | `/api/products/` | category-sql-probe | 200 | 0.011 |
| PASS | GET | `/api/products/` | tag-sql-probe | 200 | 0.01 |
| PASS | GET | `/api/products/admin` | missing-api-key | 401 | 0.008 |
| PASS | GET | `/api/products/admin` | read-route | 200 | 0.021 |
| PASS | GET | `/api/products/admin/{product_id}` | missing-api-key | 401 | 0.006 |
| PASS | GET | `/api/products/admin/{product_id}` | read-route | 200 | 0.011 |
| PASS | PATCH | `/api/products/by-sku/{sku}` | missing-api-key | 401 | 0.007 |
| SKIP | PATCH | `/api/products/by-sku/{sku}` | write-not-executed | - | 0 |
| PASS | DELETE | `/api/products/by-sku/{sku}` | missing-api-key | 401 | 0.005 |
| SKIP | DELETE | `/api/products/by-sku/{sku}` | write-not-executed | - | 0 |
| PASS | DELETE | `/api/products/clear-all` | missing-api-key | 401 | 0.004 |
| SKIP | DELETE | `/api/products/clear-all` | dangerous-operation-not-executed | - | 0 |
| PASS | POST | `/api/products/sync-meta/all` | missing-api-key | 401 | 0.004 |
| SKIP | POST | `/api/products/sync-meta/all` | dangerous-operation-not-executed | - | 0 |
| PASS | POST | `/api/products/upload-csv` | missing-api-key | 401 | 0.006 |
| SKIP | POST | `/api/products/upload-csv` | write-not-executed | - | 0 |
| PASS | PATCH | `/api/products/{product_id}` | missing-api-key | 401 | 0.006 |
| SKIP | PATCH | `/api/products/{product_id}` | write-not-executed | - | 0 |
| PASS | DELETE | `/api/products/{product_id}` | missing-api-key | 401 | 0.006 |
| SKIP | DELETE | `/api/products/{product_id}` | write-not-executed | - | 0 |
| PASS | POST | `/api/products/{product_id}/sync-meta` | missing-api-key | 401 | 0.009 |
| SKIP | POST | `/api/products/{product_id}/sync-meta` | write-not-executed | - | 0 |
| PASS | GET | `/api/products/{sku}` | read-route | 200 | 0.011 |
| PASS | GET | `/api/products/{sku}` | invalid-sku:bad-sku! | 422 | 0.006 |
| PASS | GET | `/api/products/{sku}` | invalid-sku:../etc/passwd | 404 | 0.005 |
| PASS | GET | `/api/products/{sku}` | invalid-sku:ZZNOPE999 | 404 | 0.01 |
| PASS | GET | `/health` | read-route | 200 | 0.006 |
| PASS | GET | `/webhook` | webhook-invalid-credentials | 403 | 0.006 |
| PASS | POST | `/webhook` | webhook-invalid-credentials | 403 | 0.005 |
| PASS | POST | `/api/factories/` | factory-create | 201 | 0.044 |
| PASS | GET | `/api/factories/3` | factory-read | 200 | 0.015 |
| PASS | GET | `/api/factories/` | factory-list | 200 | 0.005 |
| PASS | POST | `/api/factories/` | factory-invalid-phone | 422 | 0.003 |
| PASS | PATCH | `/api/factories/3` | factory-update | 200 | 0.03 |
| PASS | PATCH | `/api/factories/999999999` | factory-missing-update | 404 | 0.013 |
| PASS | POST | `/api/products/` | product-create | 201 | 0.063 |
| PASS | GET | `/api/products/admin/227` | product-read-by-id | 200 | 0.027 |
| PASS | GET | `/api/products/ZZTEST22813308` | product-read-by-sku | 200 | 0.013 |
| PASS | GET | `/api/products/` | product-filter-category | 200 | 0.008 |
| PASS | GET | `/api/products/` | product-filter-tag | 200 | 0.01 |
| PASS | GET | `/api/products/admin` | product-list-admin | 200 | 0.044 |
| PASS | POST | `/api/products/` | product-duplicate-sku | 400 | 0.01 |
| PASS | POST | `/api/products/` | product-invalid-category | 422 | 0.009 |
| PASS | PATCH | `/api/products/by-sku/ZZTEST22813308` | product-empty-tag | 422 | 0.008 |
| PASS | PATCH | `/api/products/227` | product-update-by-id | 200 | 0.036 |
| PASS | PATCH | `/api/products/by-sku/ZZTEST22813308` | product-update-by-sku | 200 | 0.03 |
| FAIL | PATCH | `/api/products/by-sku/ZZNOPE999` | product-missing-sku | 422 | 0.006 |
| PASS | POST | `/api/posts/` | post-create | 201 | 0.02 |
| PASS | GET | `/api/posts/2` | post-read | 200 | 0.008 |
| PASS | GET | `/api/posts/` | post-list | 200 | 0.01 |
| PASS | PATCH | `/api/posts/2` | post-update | 200 | 0.031 |
| PASS | POST | `/api/posts/` | post-empty-media | 422 | 0.006 |
| PASS | POST | `/api/posts/` | post-invalid-media-type | 422 | 0.008 |
| PASS | GET | `/api/posts/999999999` | post-missing | 404 | 0.011 |
| PASS | DELETE | `/api/posts/2` | cleanup | 200 | 0.027 |
| PASS | DELETE | `/api/products/by-sku/ZZTEST22813308` | cleanup | 200 | 0.025 |
| PASS | DELETE | `/api/factories/3` | cleanup | 200 | 0.045 |
| PASS | GET | `/api/products/admin` | verify-product-cleanup | 200 | 0.042 |

## تفاصيل الفحوص غير الناجحة

### FAIL PATCH /api/products/by-sku/ZZNOPE999 (product-missing-sku) -> 422

```text
{"detail":[{"type":"string_too_short","loc":["body","title"],"msg":"String should have at least 2 characters","input":"x","ctx":{"min_length":2}}]}
```
