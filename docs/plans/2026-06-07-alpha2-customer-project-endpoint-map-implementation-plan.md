# Customer Project Endpoint Map Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every B2B customer use one BytePlus Project with per-model endpoint mapping, while keeping admin model switches as the customer-visible access control.

**Architecture:** `users.enabled_models` controls which Relay model IDs a customer can use. `users.note.byteplus_endpoint_map` controls which BytePlus endpoint each enabled model routes to. Provisioning creates or confirms the customer Project, creates one endpoint per enabled/default model, creates one AIGC AssetGroup, then stores the endpoint map and endpoint-scoped key.

**Tech Stack:** FastAPI/Python, SQLite, ModelArk OpenAPI, BytePlus IAM OpenAPI, unittest.

---

### Task 1: Default Model Set

**Files:**
- Modify: `relay_server.py`
- Modify: `.env.relay.example`
- Test: `tests/test_model_aliases.py` or `tests/test_upstream_admin.py`

**Step 1: Write/update tests**

Assert `enabled_models=NULL` resolves to `DEFAULT_CUSTOMER_MODEL_IDS`, and that the default env value includes all `NATIVE_MODEL_IDS`.

**Step 2: Implement config**

Add `DEFAULT_CUSTOMER_MODEL_IDS`, defaulting to all `NATIVE_MODEL_IDS`, and use it in `_enabled_models_for_user`.

**Step 3: Verify**

Run:

```bash
python -m unittest tests.test_model_aliases tests.test_upstream_admin -v
```

Expected: PASS.

### Task 2: Multi-Endpoint Provisioning

**Files:**
- Modify: `relay_server.py`
- Test: `tests/test_upstream_admin.py`

**Step 1: Write/update tests**

Provisioning should call `CreateEndpoint` once per enabled/default model, write every returned endpoint into `byteplus_endpoint_map`, and call `GetApiKey` with all endpoint IDs in `ResourceIds`.

**Step 2: Implement minimal code**

Loop over `_enabled_models_for_user(user)` inside `_provision_customer_upstream_resources`. Build endpoint request bodies from the client model ID. Keep `byteplus_endpoint_id` only as the first/main compatibility endpoint.

**Step 3: Verify**

Run:

```bash
python -m unittest tests.test_upstream_admin -v
```

Expected: PASS.

### Task 3: Generation Routing Guard

**Files:**
- Modify: `relay_server.py`
- Test: `tests/test_iam_upstream.py`

**Step 1: Write/update tests**

Requests for mapped models route to the mapped endpoint. Requests for enabled but unmapped models return `endpoint_not_configured_for_model` and do not call upstream.

**Step 2: Implement guard**

Use `_user_byteplus_endpoint_id_for_model(user, client_model, real_model)` before upstream task creation. If `byteplus_endpoint_map` exists and the model is missing, raise a 400.

**Step 3: Verify**

Run:

```bash
python -m unittest tests.test_iam_upstream -v
```

Expected: PASS.

### Task 4: Admin UI Copy And State

**Files:**
- Modify: `static/admin.html`
- Test: `tests/test_static_admin_ui.py`

**Step 1: Update copy**

Replace “shared / dedicated upgrade” wording with “customer Project resources”, “model endpoint map”, and “model access switches”.

**Step 2: Expose endpoint map**

Show endpoint mapping status for enabled models and make missing mappings visible to admin.

**Step 3: Verify**

Run:

```bash
python -m unittest tests.test_static_admin_ui -v
```

Expected: PASS.

### Task 5: Model Upgrade Script

**Files:**
- Create or modify: `deploy/upgrade_model_endpoints.py`
- Test: `tests/test_upstream_admin.py` or new focused script tests

**Step 1: Add dry-run behavior**

The script should list active customers, show missing endpoint mappings for a target model, and print planned actions without writing DB.

**Step 2: Add execute behavior**

For each customer Project, create the new endpoint, wait for Running, and JSON merge the new model mapping into `users.note.byteplus_endpoint_map`.

**Step 3: Verify**

Run dry-run against a temp DB test fixture and ensure unrelated note fields survive.

### Task 6: Full Focused Gate

**Files:**
- Existing focused tests

**Step 1: Compile**

```bash
python -m py_compile relay_server.py tests/test_upstream_admin.py tests/test_iam_upstream.py
```

**Step 2: Run tests**

```bash
python -m unittest tests.test_upstream_admin tests.test_iam_upstream -v
```

Expected: PASS.

