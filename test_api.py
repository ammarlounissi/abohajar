#!/usr/bin/env python3
"""فاحص آمن لمسارات Hojrat Bladi API.

الاستخدام:
    export API_KEY='your-admin-key'
    python3 test_api.py https://api.example.com

الوضع الافتراضي للقراءة فقط:
- يقرأ OpenAPI ويفحص كل مسار معلن.
- يتحقق من حماية مسارات الإدارة دون تنفيذ عملية كتابة.
- يختبر المسارات العامة، التصفية، كشف الحقول الحساسة، والتحقق من المدخلات.
- يختبر Webhook ببيانات اعتماد وتوقيع غير صحيحين فقط.

--write ينفذ دورة CRUD على مصنع ومنتج ومنشور تجريبي ثم يحذفها، ويفحص مسارات المنشورات:
  /api/posts/published و/api/posts/unpublished وحقل deliveries والتحقق من المدخلات،
  و POST /api/posts/{id}/publish بحالات الرفض فقط (404 و422 و409).
  لا يُنفَّذ نشر فعلي أبداً (202)، فلا اتصال بـ Meta ولا منشور على الصفحة.
--include-sync يختبر مزامنة منتج واحد فقط، وقد يتصل بـ Meta.

لا يشغّل هذا الفاحص:
- DELETE /api/products/clear-all?confirm=true
- POST /api/products/sync-meta/all
- POST /api/products/upload-csv بملف صالح
- أي Webhook بتوقيع صحيح

يكتب النتائج إلى results.json وresults.md.
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BOGUS_ID = 999999999
BOGUS_SKU = "ZZNOPE999"
LEAK_FIELDS = {
    "factory_id",
    "meta_product_id",
    "sync_status",
    "sync_error_message",
}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ROUTE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}


def call(method, url, headers=None, body=None, timeout=30):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read(1_000_000).decode("utf-8", "replace")
            return response.status, time.monotonic() - started, text
    except urllib.error.HTTPError as exc:
        text = exc.read(1_000_000).decode("utf-8", "replace")
        return exc.code, time.monotonic() - started, text
    except Exception as exc:  # network and TLS errors are part of the report
        return 0, time.monotonic() - started, repr(exc)


def resolve(spec, value):
    while isinstance(value, dict) and "$ref" in value:
        node = spec
        for part in value["$ref"].lstrip("#/ ").split("/"):
            node = node[part]
        value = node
    return value


def json_body(text):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def substitute_path(path, sku, product_id):
    def replace(match):
        name = match.group(1).lower()
        if "sku" in name:
            return urllib.parse.quote(sku, safe="")
        return str(product_id)

    return re.sub(r"\{([^}]+)\}", replace, path)


def parameter_info(spec, path, operation):
    shared = spec["paths"][path].get("parameters", [])
    local = operation.get("parameters", [])
    return [resolve(spec, item) for item in shared + local]


def is_secured(spec, path, operation):
    params = parameter_info(spec, path, operation)
    return bool(operation.get("security")) or any(
        item.get("in") == "header"
        and item.get("name", "").lower() == "x-api-key"
        for item in params
    )


def required_query(spec, path, operation):
    values = {}
    for item in parameter_info(spec, path, operation):
        if item.get("in") != "query" or not item.get("required"):
            continue
        schema = resolve(spec, item.get("schema", {}))
        values[item["name"]] = BOGUS_ID if schema.get("type") == "integer" else "test"
    return values


def main():
    parser = argparse.ArgumentParser(description="Safe Hojrat Bladi API route and security tester")
    parser.add_argument("base_url")
    parser.add_argument("--key", default=os.environ.get("API_KEY", ""))
    parser.add_argument("--auth-header", default="X-API-Key")
    parser.add_argument("--sku", default="B001", help="SKU used for read-only checks")
    parser.add_argument("--product-id", type=int, default=1)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--include-sync", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    if not args.key:
        parser.error("Set API_KEY or pass --key")

    base = args.base_url.rstrip("/")
    common = {"Accept": "application/json", "ngrok-skip-browser-warning": "1"}
    authed = {**common, args.auth_header: args.key}
    results = []

    def add(method, path, check, url, status, seconds, response, verdict, note=""):
        result = {
            "method": method,
            "path": path,
            "check": check,
            "url": url,
            "status": status,
            "seconds": round(seconds, 3),
            "verdict": verdict,
            "response": (response or "")[:500],
        }
        if note:
            result["note"] = note
        results.append(result)
        print(f"[{verdict:6}] {method:6} {path} ({check}) -> {status}")

    def request(method, path, body=None, authenticated=True, query=None, headers=None):
        query_string = urllib.parse.urlencode(query or {}, doseq=True)
        url = base + path + ("?" + query_string if query_string else "")
        request_headers = dict(authed if authenticated else common)
        request_headers.update(headers or {})
        status, seconds, text = call(method, url, request_headers, body)
        return url, status, seconds, text, json_body(text)

    openapi_url = base + "/openapi.json"
    status, seconds, text = call("GET", openapi_url, common)
    if status != 200:
        sys.exit(f"Cannot fetch {openapi_url}: status={status}, response={text[:300]}")
    spec = json_body(text) or {}
    paths = spec.get("paths", {})
    if not paths:
        sys.exit("OpenAPI document does not contain any paths")
    add("GET", "/openapi.json", "openapi", openapi_url, status, seconds, text, "PASS")

    def route_exists(path, method):
        return path in paths and method.lower() in paths[path]

    # ------------------------------------------------------------------
    # Generic route inventory and safe checks.
    # ------------------------------------------------------------------
    for path in sorted(paths):
        item = paths[path]
        for method, operation in item.items():
            method = method.upper()
            if method not in ROUTE_METHODS:
                continue
            secured = is_secured(spec, path, operation)
            params = parameter_info(spec, path, operation)
            has_path_parameter = any(p.get("in") == "path" for p in params)
            has_sku = any(
                p.get("in") == "path" and "sku" in p.get("name", "").lower()
                for p in params
            )
            query = required_query(spec, path, operation)
            is_webhook = "webhook" in path.lower()
            dangerous_global = path.endswith("/sync-meta/all") or "clear-all" in path

            if is_webhook:
                if method == "GET":
                    query = {
                        "hub.mode": "subscribe",
                        "hub.verify_token": "wrong-token",
                        "hub.challenge": "123",
                    }
                    url, st, sec, body, _ = request(method, path, authenticated=False, query=query)
                else:
                    url, st, sec, body, _ = request(
                        method,
                        path,
                        body={},
                        authenticated=False,
                        headers={"X-Hub-Signature-256": "sha256=invalid"},
                    )
                add(method, path, "webhook-invalid-credentials", url, st, sec, body,
                    "PASS" if st in (401, 403) else "FAIL")
                continue

            # Missing-key check for every secured route. Bogus ids and false confirmation
            # ensure the test cannot modify an existing record if auth is accidentally open.
            if secured:
                bogus_path = substitute_path(path, BOGUS_SKU, BOGUS_ID)
                safe_query = dict(query)
                if "clear-all" in path:
                    safe_query["confirm"] = "false"
                body = {} if method in {"POST", "PUT", "PATCH"} else None
                url, st, sec, response, _ = request(
                    method, bogus_path, body=body, authenticated=False, query=safe_query
                )
                if st in (401, 403):
                    verdict = "PASS"
                elif st == 0 or st >= 500 or 200 <= st < 300:
                    verdict = "FAIL"
                else:
                    verdict = "INCONC"
                add(method, path, "missing-api-key", url, st, sec, response, verdict)

            # Never execute a global destructive or full-sync operation, even with a key.
            if dangerous_global and method in WRITE_METHODS:
                add(method, path, "dangerous-operation-not-executed", base + path, "-", 0,
                    "skipped by design", "SKIP")
                continue

            if method not in {"GET", "HEAD"}:
                add(method, path, "write-not-executed", base + path, "-", 0,
                    "use --write for CRUD lifecycle", "SKIP")
                continue

            # Happy-path reads use caller-supplied values. A 404 is a valid result when
            # the requested sample record does not exist, so it is a warning, not a failure.
            read_path = substitute_path(path, args.sku, args.product_id)
            url, st, sec, response, payload = request(method, read_path, query=query)
            verdict = "PASS" if 200 <= st < 300 else ("WARN" if 400 <= st < 500 else "FAIL")
            add(method, path, "read-route", url, st, sec, response, verdict)

            if path == "/api/products/" and method == "GET":
                # Public response must not expose administrative or factory fields.
                public_url, pst, psec, ptext, pdata = request("GET", path, authenticated=False)
                leaked = []
                if isinstance(pdata, list) and pdata:
                    leaked = sorted(LEAK_FIELDS & set(pdata[0]))
                add("GET", path, "public-response-no-sensitive-fields", public_url, pst, psec,
                    "leaked fields: " + ", ".join(leaked) if leaked else "ok",
                    "FAIL" if leaked else ("PASS" if pst == 200 else "WARN"))

                for label, params, expected in (
                    ("limit-over-max", {"limit": 501}, {400, 422}),
                    ("negative-skip", {"skip": -1}, {400, 422}),
                    ("category-sql-probe", {"category_code": "' OR 1=1 --"}, {200, 404}),
                    ("tag-sql-probe", {"tag": "' OR 1=1 --"}, {200, 404}),
                ):
                    probe_url, qst, qsec, qtext, _ = request("GET", path, authenticated=False, query=params)
                    verdict = "PASS" if qst in expected else "FAIL"
                    add("GET", path, label, probe_url, qst, qsec, qtext, verdict)

            if has_sku and method in {"GET", "HEAD"}:
                # Lowercase is valid because the API normalizes SKU values. The remaining
                # values must either be rejected or return a safe 404, never a 2xx record.
                for bad_sku in ("bad-sku!", "../etc/passwd", "ZZNOPE999"):
                    bad_path = substitute_path(path, bad_sku, args.product_id)
                    bad_url, bst, bsec, btext, _ = request(method, bad_path)
                    verdict = "PASS" if 400 <= bst < 500 else "FAIL"
                    add(method, path, f"invalid-sku:{bad_sku}", bad_url, bst, bsec, btext, verdict)

    # ------------------------------------------------------------------
    # Optional write lifecycle. Every created object is tracked for cleanup.
    # ------------------------------------------------------------------
    cleanup = []
    if args.write and not args.yes:
        answer = input("--write creates and deletes test data. Continue? [y/N] ")
        if answer.strip().lower() != "y":
            args.write = False
            print("write lifecycle cancelled")

    if args.write:
        suffix = "".join(random.choice("0123456789") for _ in range(8))
        sku = "ZZTEST" + suffix
        phone = "+213" + suffix + "00"
        media_url = f"https://hadjretbladi.com/products/ZZ/{sku}/1"
        ids = {}

        def step(label, method, path, body=None, authenticated=True, query=None, expected=(200, 299)):
            url, st, sec, response, payload = request(
                method, path, body=body, authenticated=authenticated, query=query
            )
            low, high = expected
            verdict = "PASS" if low <= st <= high else "FAIL"
            add(method, path, label, url, st, sec, response, verdict)
            return st, payload

        try:
            factory_body = {"name": "ZZ Test Factory", "phone_number": phone}
            st, payload = step("factory-create", "POST", "/api/factories/", factory_body, expected=(201, 201))
            ids["factory"] = payload.get("id") if isinstance(payload, dict) else None
            if not ids["factory"]:
                raise RuntimeError("Factory creation did not return an id")
            cleanup.append(("DELETE", f"/api/factories/{ids['factory']}"))

            step("factory-read", "GET", f"/api/factories/{ids['factory']}")
            step("factory-list", "GET", "/api/factories/")
            step("factory-invalid-phone", "POST", "/api/factories/",
                 {"name": "ZZ", "phone_number": "not-a-phone"}, expected=(422, 422))
            step("factory-update", "PATCH", f"/api/factories/{ids['factory']}", {"address": "ZZ test"})
            step("factory-missing-update", "PATCH", f"/api/factories/{BOGUS_ID}", {"address": "x"}, expected=(404, 404))

            product_body = {
                "sku": sku,
                "category_code": "ZZ",
                "tags": ["اختبار", "رخام"],
                "title": "ZZ Test Product",
                "description": "安全 test record",
                "price": 1000,
                "primary_media_url": media_url,
                "factory_id": ids["factory"],
            }
            st, payload = step("product-create", "POST", "/api/products/", product_body, expected=(201, 201))
            ids["product"] = payload.get("id") if isinstance(payload, dict) else None
            if not ids["product"]:
                raise RuntimeError("Product creation did not return an id")
            cleanup.insert(0, ("DELETE", f"/api/products/by-sku/{sku}"))
            step("product-read-by-id", "GET", f"/api/products/admin/{ids['product']}")
            step("product-read-by-sku", "GET", f"/api/products/{sku}")
            step("product-filter-category", "GET", "/api/products/", query={"category_code": "zz"})
            step("product-filter-tag", "GET", "/api/products/", query={"tag": "اختبار"})
            step("product-list-admin", "GET", "/api/products/admin", query={"limit": 500})
            step("product-duplicate-sku", "POST", "/api/products/", product_body, expected=(400, 499))
            step("product-invalid-category", "POST", "/api/products/",
                 {**product_body, "sku": sku + "XX", "category_code": "bad category"}, expected=(422, 422))
            step("product-empty-tag", "PATCH", f"/api/products/by-sku/{sku}", {"tags": [""]}, expected=(422, 422))
            step("product-update-by-id", "PATCH", f"/api/products/{ids['product']}", {"price": 1200})
            step("product-update-by-sku", "PATCH", f"/api/products/by-sku/{sku}", {"tags": ["محدث"]})
            step("product-missing-sku", "PATCH", f"/api/products/by-sku/{BOGUS_SKU}", {"title": "x"}, expected=(404, 404))
            if args.include_sync:
                step("product-sync-one", "POST", f"/api/products/{ids['product']}/sync-meta", expected=(200, 299))

            if route_exists("/api/posts/", "post"):
                post_body = {
                    "factory_id": ids["factory"],
                    "media_type": "image",
                    "media_urls": [media_url],
                }
                st, payload = step("post-create", "POST", "/api/posts/", post_body, expected=(201, 201))
                post_id = payload.get("id") if isinstance(payload, dict) else None
                if post_id:
                    cleanup.insert(0, ("DELETE", f"/api/posts/{post_id}"))
                    step("post-read", "GET", f"/api/posts/{post_id}")
                    step("post-list", "GET", "/api/posts/")
                    step("post-update", "PATCH", f"/api/posts/{post_id}", {"description": "ZZ updated"})
                    step("post-empty-media", "POST", "/api/posts/", {**post_body, "media_urls": []}, expected=(422, 422))
                    step("post-invalid-media-type", "POST", "/api/posts/", {**post_body, "media_type": "video"}, expected=(422, 422))
                    step("post-missing", "GET", f"/api/posts/{BOGUS_ID}", expected=(404, 404))

                    # --- مسارات القوائم والنشر اليدوي (حالات آمنة فقط، لا نشر فعلي) ---
                    if route_exists("/api/posts/published", "get"):
                        def check(label, path, ok, note):
                            add("GET", path, label, base + path, 200, 0, note, "PASS" if ok else "FAIL")

                        def ids_of(payload):
                            return {item.get("id") for item in payload} if isinstance(payload, list) else set()

                        step("post-list-limit-over-max", "GET", "/api/posts/published",
                             query={"limit": 501}, expected=(422, 422))
                        step("post-list-negative-offset", "GET", "/api/posts/unpublished",
                             query={"offset": -1}, expected=(422, 422))
                        step("post-invalid-channel-ids", "POST", "/api/posts/",
                             {**post_body, "channel_ids": ["Face Book!"]}, expected=(422, 422))
                        step("post-text-with-media", "POST", "/api/posts/",
                             {**post_body, "media_type": "text"}, expected=(422, 422))

                        # منشور المسودة: في غير المنشورة وليس في المنشورة، وله حقل deliveries
                        _, unpub = step("post-list-unpublished", "GET", "/api/posts/unpublished", query={"limit": 500})
                        _, pub = step("post-list-published", "GET", "/api/posts/published", query={"limit": 500})
                        check("draft-in-unpublished-only", "/api/posts/unpublished",
                              post_id in ids_of(unpub) and post_id not in ids_of(pub),
                              f"post {post_id} must be only in unpublished")
                        _, detail = step("post-detail-has-deliveries", "GET", f"/api/posts/{post_id}")
                        check("detail-deliveries-field", f"/api/posts/{post_id}",
                              isinstance(detail, dict) and detail.get("deliveries") == [],
                              "deliveries must be an empty list for an unpublished post")

                        # النشر اليدوي: الرفض فقط
                        step("post-publish-missing", "POST", f"/api/posts/{BOGUS_ID}/publish", expected=(404, 404))
                        step("post-publish-no-channels", "POST", f"/api/posts/{post_id}/publish", expected=(422, 422))
                        _, after = step("post-still-draft-after-reject", "GET", f"/api/posts/{post_id}")
                        check("rejected-publish-left-draft", f"/api/posts/{post_id}",
                              isinstance(after, dict) and after.get("status") == "draft",
                              "a rejected publish must not change status")

                        # منشور بحالة published (يُنشأ مباشرة بلا نشر فعلي): يظهر في المنشورة ويُرفض نشره بـ 409
                        st, done = step("post-create-published", "POST", "/api/posts/",
                                        {**post_body, "status": "published"},
                                        expected=(201, 201))
                        done_id = done.get("id") if isinstance(done, dict) else None
                        if done_id:
                            cleanup.insert(0, ("DELETE", f"/api/posts/{done_id}"))
                            _, pub = step("post-list-published-2", "GET", "/api/posts/published", query={"limit": 500})
                            _, unpub = step("post-list-unpublished-2", "GET", "/api/posts/unpublished", query={"limit": 500})
                            check("published-in-published-only", "/api/posts/published",
                                  done_id in ids_of(pub) and done_id not in ids_of(unpub),
                                  f"post {done_id} must be only in published")
                            step("post-publish-already-published", "POST", f"/api/posts/{done_id}/publish",
                                 expected=(409, 409))
        finally:
            for method, path in cleanup:
                step("cleanup", method, path, expected=(200, 299))
            if sku:
                _, payload = step("verify-product-cleanup", "GET", "/api/products/admin", query={"limit": 500})
                left = isinstance(payload, list) and any(item.get("sku") == sku for item in payload)
                if left:
                    add("GET", "/api/products/admin", "leftover-test-product", base, 200, 0,
                        f"delete {sku} manually", "FAIL")

    counts = {}
    for result in results:
        counts[result["verdict"]] = counts.get(result["verdict"], 0) + 1
    with open("results.json", "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    with open("results.md", "w", encoding="utf-8") as handle:
        handle.write(f"# تقرير فحص API: {base}\n\nملخص: `{counts}`\n\n")
        handle.write("| النتيجة | الطريقة | المسار | الفحص | الحالة | الثواني |\n|---|---|---|---|---:|---:|\n")
        for result in results:
            handle.write(
                f"| {result['verdict']} | {result['method']} | `{result['path']}` | "
                f"{result['check']} | {result['status']} | {result['seconds']} |\n"
            )
        handle.write("\n## تفاصيل الفحوص غير الناجحة\n")
        for result in results:
            if result["verdict"] not in {"PASS", "SKIP"}:
                handle.write(
                    f"\n### {result['verdict']} {result['method']} {result['path']} "
                    f"({result['check']}) -> {result['status']}\n\n```text\n"
                    f"{result['response']}\n```\n"
                )
    print("\nSummary:", counts)
    print("Saved results.md and results.json")


if __name__ == "__main__":
    main()
