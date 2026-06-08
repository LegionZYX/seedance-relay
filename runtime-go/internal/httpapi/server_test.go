package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"seedance-runtime/internal/config"
	"seedance-runtime/internal/store"
)

func newTestServer(t *testing.T, upstream http.HandlerFunc) (*httptest.Server, *store.DB, string) {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "relay.sqlite"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	upstreamServer := httptest.NewServer(upstream)
	t.Cleanup(upstreamServer.Close)

	srv := NewServer(config.Config{
		DBPath:          "unused",
		ListenAddr:      "127.0.0.1:0",
		PublicDomain:    "media.example.test",
		UpstreamBaseURL: upstreamServer.URL,
		UpstreamAPIKey:  "ark-runtime-test",
	}, db, upstreamServer.Client())
	return httptest.NewServer(srv.Routes()), db, upstreamServer.URL
}

func newTestServerWithControlPlane(t *testing.T, upstream http.HandlerFunc, controlPlane http.HandlerFunc) (*httptest.Server, *store.DB) {
	t.Helper()
	db, err := store.Open(filepath.Join(t.TempDir(), "relay.sqlite"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	upstreamServer := httptest.NewServer(upstream)
	t.Cleanup(upstreamServer.Close)
	controlPlaneServer := httptest.NewServer(controlPlane)
	t.Cleanup(controlPlaneServer.Close)

	srv := NewServer(config.Config{
		DBPath:               "unused",
		ListenAddr:           "127.0.0.1:0",
		PublicDomain:         "media.example.test",
		UpstreamBaseURL:      upstreamServer.URL,
		UpstreamAPIKey:       "ark-runtime-test",
		ControlPlaneBaseURL:  controlPlaneServer.URL,
		RuntimeInternalToken: "runtime-internal-test",
	}, db, upstreamServer.Client())
	return httptest.NewServer(srv.Routes()), db
}

func TestModelsFiltersByCustomerEnabledModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-models", "u_models", `["seedance-1-0-lite-t2v-250428"]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-models")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"id":"seedance-1-0-lite-t2v-250428"`) {
		t.Fatalf("body missing enabled model: %s", body)
	}
	if strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("body leaked disabled model: %s", body)
	}
}

func TestModelsInvalidPersistedEnabledModelsDoNotFallBackToAllModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-dirty-models", "u_dirty_models", `["unknown-byteplus-model"]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-dirty-models")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"data":[]`) {
		t.Fatalf("body should have empty model list: %s", body)
	}
	if strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("body leaked default or upstream model: %s", body)
	}
}

func TestModelsEmptyPersistedEnabledModelsDoNotFallBackToAllModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-empty-models", "u_empty_models", `[]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-empty-models")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"data":[]`) {
		t.Fatalf("body should have empty model list: %s", body)
	}
}

func TestModelsMalformedPersistedEnabledModelsDoNotFallBackToAllModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-bad-model-json", "u_bad_model_json", `{bad json`); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	user, err := db.UserByAPIKey("sk-bad-model-json")
	if err != nil {
		t.Fatalf("read user: %v", err)
	}
	if user == nil || !user.EnabledModelsSet || len(user.EnabledModels) != 0 {
		t.Fatalf("malformed enabled_models should be explicit empty access: %#v", user)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-bad-model-json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"data":[]`) {
		t.Fatalf("body should have empty model list: %s", body)
	}

	estimateBody := strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"must not be priced"}]
	}`)
	estimateReq, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", estimateBody)
	estimateReq.Header.Set("Authorization", "Bearer sk-bad-model-json")
	estimateReq.Header.Set("Content-Type", "application/json")
	estimateResp, err := http.DefaultClient.Do(estimateReq)
	if err != nil {
		t.Fatalf("request estimate: %v", err)
	}
	defer estimateResp.Body.Close()
	estimateRaw, _ := io.ReadAll(estimateResp.Body)

	if estimateResp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d body=%s", estimateResp.StatusCode, estimateRaw)
	}
	if !strings.Contains(string(estimateRaw), "model_not_enabled") {
		t.Fatalf("body missing model_not_enabled: %s", estimateRaw)
	}
}

func TestModelsWithoutAuthReturnsAllModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	resp, err := http.Get(server.URL + "/v1/models")
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"id":"seedance-1-0-lite-t2v-250428"`) || !strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("body missing public models: %s", body)
	}
}

func TestModelsReturnSafeCapabilityFieldsWithoutUpstreamDetails(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	resp, err := http.Get(server.URL + "/v1/models")
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	raw := string(body)
	for _, required := range []string{"supported_resolutions", "supported_ratios", "duration_seconds", "capabilities"} {
		if !strings.Contains(raw, required) {
			t.Fatalf("body missing %s: %s", required, body)
		}
	}
	for _, forbidden := range []string{"upstream_model", "upstream_model_or_endpoint", "operator_notes", "byteplus", "ark-"} {
		if strings.Contains(raw, forbidden) {
			t.Fatalf("body leaked %s: %s", forbidden, body)
		}
	}
}

func TestModelsRejectsInvalidBearerInsteadOfFallingBackToPublicModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer sk-old-disabled")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("invalid bearer received public model list: %s", body)
	}
}

func TestModelsRejectsMalformedAuthorizationInsteadOfFallingBackToPublicModels(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "missing_auth") {
		t.Fatalf("body missing missing_auth: %s", body)
	}
	if strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("malformed authorization received public model list: %s", body)
	}
}

func TestModelsAcceptsRelaySessionCookie(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-cookie-models", "u_cookie_models", `["seedance-1-0-lite-t2v-250428"]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_models", "u_cookie_models"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/models", nil)
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_models"})
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"id":"seedance-1-0-lite-t2v-250428"`) {
		t.Fatalf("body missing enabled model: %s", body)
	}
	if strings.Contains(string(body), `"id":"dreamina-seedance-2-0-260128"`) {
		t.Fatalf("body leaked disabled model: %s", body)
	}
}

func TestEstimateAppliesCustomerPriceMultiplier(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-estimate", "u_estimate", "", 0.45, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	body := bytes.NewBufferString(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"A quiet street walk."}],
		"resolution":"480p",
		"ratio":"16:9",
		"duration":5,
		"generate_audio":false
	}`)
	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", body)
	req.Header.Set("Authorization", "Bearer sk-estimate")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode estimate: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%v", resp.StatusCode, payload)
	}
	if payload["estimated_tokens"].(float64) != 50640 {
		t.Fatalf("estimated_tokens = %v", payload["estimated_tokens"])
	}
	if payload["estimated_cost_usd"].(float64) != 0.425376 {
		t.Fatalf("estimated_cost_usd = %v", payload["estimated_cost_usd"])
	}
	if payload["max_cost_usd"].(float64) != 0.467914 {
		t.Fatalf("max_cost_usd = %v", payload["max_cost_usd"])
	}
	if payload["price_multiplier"].(float64) != 1.2 {
		t.Fatalf("price_multiplier = %v", payload["price_multiplier"])
	}
	if payload["can_afford"].(bool) {
		t.Fatalf("can_afford should be false for balance below max hold")
	}
	if payload["shortage_usd"].(float64) != 0.017914 {
		t.Fatalf("shortage_usd = %v", payload["shortage_usd"])
	}
}

func TestEstimateRejectsDisabledModelBeforePricing(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-disabled-estimate", "u_disabled_estimate", `["seedance-1-0-lite-t2v-250428"]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{"model":"dreamina-seedance-2-0-260128","duration":5}`))
	req.Header.Set("Authorization", "Bearer sk-disabled-estimate")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "model_not_enabled") {
		t.Fatalf("body missing model_not_enabled: %s", body)
	}
}

func TestRuntimeErrorUsesFastAPICompatibleDetailEnvelope(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-error-envelope", "u_error_envelope", `["seedance-1-0-lite-t2v-250428"]`); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{"model":"dreamina-seedance-2-0-260128","duration":5}`))
	req.Header.Set("Authorization", "Bearer sk-error-envelope")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, exists := payload["error"]; exists {
		t.Fatalf("Go runtime errors must use detail.error envelope, got top-level error: %#v", payload)
	}
	detail, ok := payload["detail"].(map[string]any)
	if !ok {
		t.Fatalf("missing detail object: %#v", payload)
	}
	errBody, ok := detail["error"].(map[string]any)
	if !ok {
		t.Fatalf("missing detail.error object: %#v", payload)
	}
	if errBody["code"] != "model_not_enabled" {
		t.Fatalf("error code = %#v, payload=%#v", errBody["code"], payload)
	}
}

func TestRuntimeMethodNotAllowedUsesJSONErrorEnvelope(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/models", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request models: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("status = %d payload=%#v", resp.StatusCode, payload)
	}
	detail, ok := payload["detail"].(map[string]any)
	if !ok {
		t.Fatalf("missing detail object: %#v", payload)
	}
	errBody, ok := detail["error"].(map[string]any)
	if !ok || errBody["code"] != "method_not_allowed" {
		t.Fatalf("missing method_not_allowed detail.error: %#v", payload)
	}
}

func TestRuntimeNotFoundUsesJSONErrorEnvelope(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/bad/path", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request bad path: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d payload=%#v", resp.StatusCode, payload)
	}
	detail, ok := payload["detail"].(map[string]any)
	if !ok {
		t.Fatalf("missing detail object: %#v", payload)
	}
	errBody, ok := detail["error"].(map[string]any)
	if !ok || errBody["code"] != "not_found" {
		t.Fatalf("missing not_found detail.error: %#v", payload)
	}
}

func TestEstimateRejectsUnknownContentBlockTypeBeforePricing(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-invalid-estimate", "u_invalid_estimate", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"unsafe_unknown_block","text":"not a native block"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-invalid-estimate")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "invalid_content_block") {
		t.Fatalf("body missing invalid_content_block: %s", body)
	}
}

func TestEstimateRejectsI2VModelWithoutVisualReferenceBeforePricing(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-i2v-estimate", "u_i2v_estimate", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{
		"model":"seedance-1-0-lite-i2v-250428",
		"content":[
			{"type":"text","text":"Animate this without a visual reference."},
			{"type":"audio_url","audio_url":{"url":"https://cdn.example.test/ref.mp3"},"role":"reference_audio"}
		],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-i2v-estimate")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "visual_reference_required") {
		t.Fatalf("body missing visual_reference_required: %s", body)
	}
}

func TestEstimateRejectsUnsupportedResolutionBeforePricing(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-resolution-estimate", "u_resolution_estimate", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"Invalid resolution"}],
		"resolution":"8k",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-resolution-estimate")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "unsupported_resolution") {
		t.Fatalf("body missing unsupported_resolution: %s", body)
	}
}

func TestEstimateAcceptsRelaySessionCookie(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-cookie-estimate", "u_cookie_estimate", "", 100, 1.3); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_estimate", "u_cookie_estimate"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos/estimate", strings.NewReader(`{"model":"dreamina-seedance-2-0-260128","resolution":"480p","duration":5}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_estimate"})
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("estimate request: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode estimate: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%v", resp.StatusCode, payload)
	}
	if payload["price_multiplier"].(float64) != 1.3 {
		t.Fatalf("price_multiplier = %v", payload["price_multiplier"])
	}
}

func TestCreateVideoPostsNativePayloadAndHoldsBalance(t *testing.T) {
	var gotAuth string
	var gotPayload map[string]any
	var db *store.DB
	var balanceAtUpstream float64
	server, openedDB, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		var err error
		balanceAtUpstream, err = db.TestUserBalance("u_create")
		if err != nil {
			t.Fatalf("balance at upstream: %v", err)
		}
		if r.URL.Path != "/contents/generations/tasks" {
			t.Fatalf("upstream path = %s", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-created"})
	})
	db = openedDB
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-create", "u_create", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[
			{"type":"text","text":"A product walks through a clean studio."},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref.jpg"},"role":"reference_image"}
		],
		"resolution":"480p",
		"ratio":"9:16",
		"duration":5,
		"seed":123,
		"watermark":false,
		"generate_audio":false
	}`))
	req.Header.Set("Authorization", "Bearer sk-create")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()

	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("decode create response: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%v", resp.StatusCode, payload)
	}
	if gotAuth != "Bearer ark-user-create" {
		t.Fatalf("upstream auth = %q", gotAuth)
	}
	if gotPayload["model"] != "dreamina-seedance-2-0-260128" {
		t.Fatalf("upstream model = %v", gotPayload["model"])
	}
	if gotPayload["ratio"] != "9:16" {
		t.Fatalf("upstream ratio = %v", gotPayload["ratio"])
	}
	if balanceAtUpstream != 9.532086 {
		t.Fatalf("balance at upstream = %v", balanceAtUpstream)
	}
	if payload["held_usd"].(float64) != 0.467914 {
		t.Fatalf("held_usd = %v", payload["held_usd"])
	}
	balance, err := db.TestUserBalance("u_create")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.532086 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestCreateVideoForwardsDefaultDurationUsedForBilling(t *testing.T) {
	var gotPayload map[string]any
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-default-duration"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-default-duration", "u_default_duration", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"Default duration should be explicit upstream."}],
		"resolution":"480p"
	}`))
	req.Header.Set("Authorization", "Bearer sk-default-duration")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotPayload["duration"] != float64(5) {
		t.Fatalf("upstream duration should match billed default 5, got %#v in %#v", gotPayload["duration"], gotPayload)
	}
}

func TestCreateVideoForwardsDefaultWatermarkFalse(t *testing.T) {
	var gotPayload map[string]any
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&gotPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-default-watermark"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-default-watermark", "u_default_watermark", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"Default watermark should be explicit upstream."}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-default-watermark")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotPayload["watermark"] != false {
		t.Fatalf("upstream watermark should default false, got %#v in %#v", gotPayload["watermark"], gotPayload)
	}
}

func TestCreateVideoRejectsCrossSiteCookieRequestBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
		writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-cookie-create", "u_cookie_create", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_create", "u_cookie_create"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"cross site attempt"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_create"})
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "https://evil.example.test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "csrf_origin_mismatch") {
		t.Fatalf("body missing csrf error: %s", body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream calls = %d", upstreamCalls)
	}
}

func TestCreateVideoRejectsMalformedBearerWithCookieViaCSRFBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
		writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-cookie-malformed", "u_cookie_malformed", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_malformed_create", "u_cookie_malformed"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"malformed bearer should not bypass csrf"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_malformed_create"})
	req.Header.Set("Authorization", "Bearer   ")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "https://evil.example.test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "csrf_origin_mismatch") {
		t.Fatalf("body missing csrf error: %s", body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream calls = %d", upstreamCalls)
	}
}

func TestCreateVideoRejectsMalformedAuthorizationInsteadOfFallingBackToCookie(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
		writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-cookie-auth-fallback", "u_cookie_auth_fallback", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_auth_fallback", "u_cookie_auth_fallback"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"malformed authorization should fail auth"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_auth_fallback"})
	req.Header.Set("Authorization", "Bearer")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://"+strings.TrimPrefix(server.URL, "http://"))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "missing_auth") {
		t.Fatalf("body missing auth error: %s", body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream calls = %d", upstreamCalls)
	}
}

func TestCreateVideoRejectsCookieRequestWithMismatchedOriginScheme(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
		writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-cookie-scheme", "u_cookie_scheme", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_scheme_create", "u_cookie_scheme"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"scheme mismatch attempt"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_scheme_create"})
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "http://media.example.test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "csrf_origin_mismatch") {
		t.Fatalf("body missing csrf error: %s", body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream calls = %d", upstreamCalls)
	}
}

func TestCreateVideoAllowsSameOriginCookieRequest(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-cookie-created"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey("sk-cookie-same-origin", "u_cookie_same_origin", "", 10, 1.2, "ark-user-create"); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_same_origin_create", "u_cookie_same_origin"); err != nil {
		t.Fatalf("insert session: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"same origin request"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_same_origin_create"})
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "https://media.example.test")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
}

func TestCreateVideoRejectsInsufficientBalanceBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-low-balance", "u_low_balance", "", 0.1, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"A quiet street walk."}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-low-balance")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusPaymentRequired {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
	if !strings.Contains(string(body), "insufficient_balance") {
		t.Fatalf("body missing insufficient_balance: %s", body)
	}
}

func TestCreateVideoRejectsInsufficientBalanceBeforeControlPlanePrepare(t *testing.T) {
	upstreamCalls := 0
	prepareCalls := 0
	server, db := newTestServerWithControlPlane(
		t,
		func(w http.ResponseWriter, r *http.Request) {
			upstreamCalls++
			writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
		},
		func(w http.ResponseWriter, r *http.Request) {
			prepareCalls++
			writeJSON(w, http.StatusOK, map[string]any{"content": []map[string]any{
				{"type": "text", "text": "prepared side effect should not happen"},
			}})
		},
	)
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-low-prepare", "u_low_prepare", "", 0.1, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"low balance should stop before prepare"}],
		"extra_body":{"real_person_mode":true},
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-low-prepare")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusPaymentRequired {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "insufficient_balance") {
		t.Fatalf("body missing insufficient_balance: %s", body)
	}
	if prepareCalls != 0 {
		t.Fatalf("control-plane prepare calls = %d", prepareCalls)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream calls = %d", upstreamCalls)
	}
}

func TestCreateVideoUpstreamErrorReturnsSafeRequestIDWithoutSecretLeakage(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Request-Id", "req_safe_go_123")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte("blocked https://byteplus.example.test/private-video.mp4 ark-customer-secret-key"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-upstream-error", "u_upstream_error", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"A valid customer request."}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-upstream-error")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	raw := string(body)
	if !strings.Contains(raw, `"request_id":"req_safe_go_123"`) {
		t.Fatalf("body missing upstream request id: %s", body)
	}
	for _, leaked := range []string{"byteplus.example.test", "private-video.mp4", "ark-customer-secret-key"} {
		if strings.Contains(raw, leaked) {
			t.Fatalf("body leaked %s: %s", leaked, body)
		}
	}
	balance, err := db.TestUserBalance("u_upstream_error")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 10 {
		t.Fatalf("balance should be refunded after upstream error, got %v", balance)
	}
}

func TestCreateVideoInvalidUpstreamURLRefundsReservedBalance(t *testing.T) {
	db, err := store.Open(filepath.Join(t.TempDir(), "relay.sqlite"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()
	srv := NewServer(config.Config{
		DBPath:          "unused",
		ListenAddr:      "127.0.0.1:0",
		PublicDomain:    "media.example.test",
		UpstreamBaseURL: "http://%",
		UpstreamAPIKey:  "ark-runtime-test",
	}, db, http.DefaultClient)
	server := httptest.NewServer(srv.Routes())
	defer server.Close()

	if err := db.InsertTestUserWithBalance("sk-invalid-upstream-url", "u_invalid_upstream_url", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"A valid customer request."}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-invalid-upstream-url")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "upstream_error") {
		t.Fatalf("body missing upstream_error: %s", body)
	}
	balance, err := db.TestUserBalance("u_invalid_upstream_url")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 10 {
		t.Fatalf("balance should be refunded after invalid upstream URL, got %v", balance)
	}
}

func TestCreateVideoCancelsUpstreamTaskAndRefundsWhenLocalTaskWriteFails(t *testing.T) {
	deleteCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/contents/generations/tasks":
			writeJSON(w, http.StatusOK, map[string]string{"id": "duplicate-upstream-task"})
		case r.Method == http.MethodDelete && r.URL.Path == "/contents/generations/tasks/duplicate-upstream-task":
			deleteCalls++
			writeJSON(w, http.StatusOK, map[string]any{"ok": true})
		default:
			t.Fatalf("unexpected upstream request: %s %s", r.Method, r.URL.Path)
		}
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-local-write-fails", "u_local_write_fails", "", 10, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_existing_duplicate", "u_local_write_fails", "duplicate-upstream-task", 0.1, 1.0); err != nil {
		t.Fatalf("insert duplicate upstream task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"local task insert should fail"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-local-write-fails")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if deleteCalls != 1 {
		t.Fatalf("upstream delete calls = %d", deleteCalls)
	}
	balance, err := db.TestUserBalance("u_local_write_fails")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 10 {
		t.Fatalf("balance should be refunded after local write failure, got %v", balance)
	}
}

func TestCreateVideoRejectsUnknownContentBlockTypeBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-invalid-content", "u_invalid_content", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"unsafe_unknown_block","text":"not a native block"}],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-invalid-content")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
	if !strings.Contains(string(body), "invalid_content_block") {
		t.Fatalf("body missing invalid_content_block: %s", body)
	}
}

func TestCreateVideoRejectsTooManyReferenceImagesBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-too-many-images", "u_too_many_images", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[
			{"type":"text","text":"Use too many references."},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-0.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-1.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-2.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-3.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-4.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-5.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-6.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-7.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-8.jpg"},"role":"reference_image"},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref-9.jpg"},"role":"reference_image"}
		],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-too-many-images")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
	if !strings.Contains(string(body), "too_many_reference_images") {
		t.Fatalf("body missing too_many_reference_images: %s", body)
	}
}

func TestCreateVideoRejectsUnknownContentRoleBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-invalid-role", "u_invalid_role", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[
			{"type":"text","text":"Use an invalid role."},
			{"type":"image_url","image_url":{"url":"https://cdn.example.test/ref.jpg"},"role":"unsupported_reference_role"}
		],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-invalid-role")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
	if !strings.Contains(string(body), "invalid_content_role") {
		t.Fatalf("body missing invalid_content_role: %s", body)
	}
}

func TestCreateVideoRejectsI2VModelWithoutVisualReferenceBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-i2v-create", "u_i2v_create", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"seedance-1-0-lite-i2v-250428",
		"content":[
			{"type":"text","text":"Animate this without a visual reference."},
			{"type":"audio_url","audio_url":{"url":"https://cdn.example.test/ref.mp3"},"role":"reference_audio"}
		],
		"resolution":"480p",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-i2v-create")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
	if !strings.Contains(string(body), "visual_reference_required") {
		t.Fatalf("body missing visual_reference_required: %s", body)
	}
}

func TestCreateVideoRejectsUnsupportedRatioBeforeUpstream(t *testing.T) {
	upstreamCalls := 0
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls++
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-ratio-create", "u_ratio_create", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"Invalid ratio"}],
		"resolution":"480p",
		"ratio":"4:3",
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-ratio-create")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "unsupported_ratio") {
		t.Fatalf("body missing unsupported_ratio: %s", body)
	}
	if upstreamCalls != 0 {
		t.Fatalf("upstream called %d times", upstreamCalls)
	}
}

func TestCreateVideoPassesPromptTextWithoutRelayContentCensorship(t *testing.T) {
	prompt := "An adult-themed signed customer scene request with dramatic lighting."
	var gotUpstreamPayload map[string]any
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&gotUpstreamPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-prompt-pass"})
	}, func(w http.ResponseWriter, r *http.Request) {
		var gotPreparePayload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"content": gotPreparePayload["content"],
		})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-prompt-pass", "u_prompt_pass", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	reqBody, _ := json.Marshal(map[string]any{
		"model":    "dreamina-seedance-2-0-260128",
		"content":  []map[string]any{{"type": "text", "text": prompt}},
		"duration": 5,
	})
	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", bytes.NewReader(reqBody))
	req.Header.Set("Authorization", "Bearer sk-prompt-pass")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	content := gotUpstreamPayload["content"].([]any)
	textBlock := content[0].(map[string]any)
	if textBlock["text"] != prompt {
		t.Fatalf("prompt text was changed or blocked: %#v", gotUpstreamPayload)
	}
}

func TestCreateVideoUsesCustomerEndpointFromUserNote(t *testing.T) {
	var gotAuthorization string
	var gotUpstreamPayload map[string]any
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuthorization = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&gotUpstreamPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-customer-endpoint"})
	}, func(w http.ResponseWriter, r *http.Request) {
		var gotPreparePayload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"content": gotPreparePayload["content"]})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey(
		"sk-customer-endpoint",
		"u_customer_endpoint",
		`["dreamina-seedance-2-0-260128"]`,
		10,
		1.0,
		"customer-endpoint-key",
	); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.SetTestUserNote("u_customer_endpoint", `{"byteplus_endpoint_id":"ep-customer-dedicated"}`); err != nil {
		t.Fatalf("set note: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"customer endpoint routing"}],
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-customer-endpoint")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotAuthorization != "Bearer customer-endpoint-key" {
		t.Fatalf("authorization = %q", gotAuthorization)
	}
	if gotUpstreamPayload["model"] != "ep-customer-dedicated" {
		t.Fatalf("upstream model = %#v payload=%#v", gotUpstreamPayload["model"], gotUpstreamPayload)
	}
}

func TestCreateVideoRoutesCustomerEndpointMapByClientModel(t *testing.T) {
	var gotAuthorization string
	var gotUpstreamPayload map[string]any
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuthorization = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&gotUpstreamPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-customer-endpoint-map"})
	}, func(w http.ResponseWriter, r *http.Request) {
		var gotPreparePayload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"content": gotPreparePayload["content"]})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey(
		"sk-customer-endpoint-map",
		"u_customer_endpoint_map",
		`["dreamina-seedance-2-0-260128","seedance-1-5-pro-251215"]`,
		10,
		1.0,
		"customer-map-key",
	); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.SetTestUserNote("u_customer_endpoint_map", `{
		"byteplus_endpoint_id":"ep-default-standard",
		"byteplus_endpoint_map":{
			"dreamina-seedance-2-0-260128":"ep-standard",
			"seedance-1-5-pro-251215":"ep-seedance15"
		}
	}`); err != nil {
		t.Fatalf("set note: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"seedance-1-5-pro-251215",
		"content":[{"type":"text","text":"customer endpoint map routing"}],
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-customer-endpoint-map")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotAuthorization != "Bearer customer-map-key" {
		t.Fatalf("authorization = %q", gotAuthorization)
	}
	if gotUpstreamPayload["model"] != "ep-seedance15" {
		t.Fatalf("upstream model = %#v payload=%#v", gotUpstreamPayload["model"], gotUpstreamPayload)
	}
}

func TestCreateVideoUsesEndpointKeyMapForSelectedModel(t *testing.T) {
	var gotAuthorization string
	var gotUpstreamPayload map[string]any
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		gotAuthorization = r.Header.Get("Authorization")
		if err := json.NewDecoder(r.Body).Decode(&gotUpstreamPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-customer-endpoint-key-map"})
	}, func(w http.ResponseWriter, r *http.Request) {
		var gotPreparePayload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"content": gotPreparePayload["content"]})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey(
		"sk-customer-endpoint-key-map",
		"u_customer_endpoint_key_map",
		`["dreamina-seedance-2-0-260128","dreamina-seedance-2-0-fast-260128"]`,
		10,
		1.0,
		"legacy-map-key",
	); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.SetTestUserNote("u_customer_endpoint_key_map", `{
		"byteplus_endpoint_id":"ep-default-standard",
		"byteplus_endpoint_map":{
			"dreamina-seedance-2-0-260128":"ep-standard",
			"dreamina-seedance-2-0-fast-260128":"ep-fast"
		},
		"byteplus_endpoint_key_map":{
			"dreamina-seedance-2-0-260128":{"endpoint_id":"ep-standard","api_key":"standard-endpoint-key","expires_at":3333333333},
			"dreamina-seedance-2-0-fast-260128":{"endpoint_id":"ep-fast","api_key":"fast-endpoint-key","expires_at":3333333333}
		},
		"byteplus_endpoint_key_mode":"per_endpoint"
	}`); err != nil {
		t.Fatalf("set note: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-fast-260128",
		"content":[{"type":"text","text":"selected endpoint key routing"}],
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-customer-endpoint-key-map")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotAuthorization != "Bearer fast-endpoint-key" {
		t.Fatalf("authorization = %q", gotAuthorization)
	}
	if gotUpstreamPayload["model"] != "ep-fast" {
		t.Fatalf("upstream model = %#v payload=%#v", gotUpstreamPayload["model"], gotUpstreamPayload)
	}
}

func TestCreateVideoRejectsUnmappedCustomerEndpointMapModel(t *testing.T) {
	upstreamCalled := false
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalled = true
		writeJSON(w, http.StatusOK, map[string]string{"id": "should-not-create"})
	}, func(w http.ResponseWriter, r *http.Request) {
		var gotPreparePayload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"content": gotPreparePayload["content"]})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithUpstreamKey(
		"sk-customer-endpoint-map-missing",
		"u_customer_endpoint_map_missing",
		`["dreamina-seedance-2-0-260128","dreamina-seedance-2-0-fast-260128"]`,
		10,
		1.0,
		"customer-map-key",
	); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.SetTestUserNote("u_customer_endpoint_map_missing", `{
		"byteplus_endpoint_id":"ep-default-standard",
		"byteplus_endpoint_map":{
			"dreamina-seedance-2-0-260128":"ep-standard"
		}
	}`); err != nil {
		t.Fatalf("set note: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-fast-260128",
		"content":[{"type":"text","text":"must not fall back"}],
		"duration":5
	}`))
	req.Header.Set("Authorization", "Bearer sk-customer-endpoint-map-missing")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "endpoint_not_configured_for_model") {
		t.Fatalf("body missing endpoint_not_configured_for_model: %s", body)
	}
	if upstreamCalled {
		t.Fatalf("unmapped endpoint map request called upstream")
	}
}

func TestCreateVideoDelegatesRealPersonMaterializationBeforeUpstream(t *testing.T) {
	var gotPrepareAuth string
	var gotPreparePayload map[string]any
	var gotUpstreamPayload map[string]any
	server, db := newTestServerWithControlPlane(t, func(w http.ResponseWriter, r *http.Request) {
		if err := json.NewDecoder(r.Body).Decode(&gotUpstreamPayload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]string{"id": "upstream-real-person"})
	}, func(w http.ResponseWriter, r *http.Request) {
		gotPrepareAuth = r.Header.Get("X-Runtime-Token")
		if r.URL.Path != "/internal/runtime/prepare-video-content" {
			t.Fatalf("control-plane path = %s", r.URL.Path)
		}
		if err := json.NewDecoder(r.Body).Decode(&gotPreparePayload); err != nil {
			t.Fatalf("decode prepare payload: %v", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"content": []map[string]any{
				{"type": "text", "text": "A portrait walks."},
				{
					"type":      "image_url",
					"image_url": map[string]any{"url": "asset://asset-image"},
					"role":      "reference_image",
				},
			},
		})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-real-person", "u_real_person", "", 10, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}

	req, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/videos", strings.NewReader(`{
		"model":"dreamina-seedance-2-0-260128",
		"content":[{"type":"text","text":"A portrait walks."}],
		"extra_body":{"real_person_mode":true}
	}`))
	req.Header.Set("Authorization", "Bearer sk-real-person")
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("create request: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if gotPrepareAuth != "runtime-internal-test" {
		t.Fatalf("prepare auth = %q", gotPrepareAuth)
	}
	if gotPreparePayload["user_id"] != "u_real_person" {
		t.Fatalf("prepare user_id = %v", gotPreparePayload["user_id"])
	}
	content := gotUpstreamPayload["content"].([]any)
	second := content[1].(map[string]any)
	imageURL := second["image_url"].(map[string]any)
	if imageURL["url"] != "asset://asset-image" {
		t.Fatalf("upstream content not materialized: %#v", gotUpstreamPayload)
	}
}

func TestGetVideoRefreshesSucceededTaskAndHidesUpstreamURL(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/contents/generations/tasks/upstream-success" {
			t.Fatalf("upstream path = %s", r.URL.Path)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"status":     "succeeded",
			"model":      "dreamina-seedance-2-0-260128",
			"resolution": "480p",
			"usage": map[string]any{
				"completion_tokens": 1000,
			},
			"content": map[string]any{
				"video_url": "https://byteplus.example.test/private-video.mp4",
			},
		})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-get-success", "u_get_success", "", 9.532086, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_success", "u_get_success", "upstream-success", 0.467914, 1.2); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_success", nil)
	req.Header.Set("Authorization", "Bearer sk-get-success")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if strings.Contains(string(body), "byteplus.example.test") {
		t.Fatalf("response leaked upstream URL: %s", body)
	}
	if !strings.Contains(string(body), `"video_url":"https://media.example.test/v1/videos/vid_success/content"`) {
		t.Fatalf("response missing relay video_url: %s", body)
	}
	if !strings.Contains(string(body), `"actual_cost_usd":0.0084`) {
		t.Fatalf("response missing customer actual cost: %s", body)
	}
	balance, err := db.TestUserBalance("u_get_success")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.9916 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestGetVideoSettlementUsesTaskPriceMultiplierSnapshot(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":     "succeeded",
			"model":      "dreamina-seedance-2-0-260128",
			"resolution": "480p",
			"usage": map[string]any{
				"completion_tokens": 1000,
			},
			"content": map[string]any{
				"video_url": "https://byteplus.example.test/private-video.mp4",
			},
		})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-snapshot-settle", "u_snapshot_settle", "", 9.532086, 1.2); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_snapshot_settle", "u_snapshot_settle", "upstream-snapshot-settle", 0.467914, 1.2); err != nil {
		t.Fatalf("insert task: %v", err)
	}
	if err := db.SetTestUserPriceMultiplier("u_snapshot_settle", 1.8); err != nil {
		t.Fatalf("change multiplier: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_snapshot_settle", nil)
	req.Header.Set("Authorization", "Bearer sk-snapshot-settle")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"actual_cost_usd":0.0084`) {
		t.Fatalf("settlement should use task multiplier snapshot, body=%s", body)
	}
	if strings.Contains(string(body), `"actual_cost_usd":0.0126`) {
		t.Fatalf("settlement used latest customer multiplier: %s", body)
	}
	balance, err := db.TestUserBalance("u_snapshot_settle")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.9916 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestGetVideoSettlementUsesTaskModelSnapshot(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"status":     "succeeded",
			"model":      "seedance-1-0-pro-fast-251015",
			"resolution": "480p",
			"usage": map[string]any{
				"completion_tokens": 1000,
			},
			"content": map[string]any{
				"video_url": "https://byteplus.example.test/private-video.mp4",
			},
		})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-model-snapshot-settle", "u_model_snapshot_settle", "", 9.532086, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_model_snapshot_settle", "u_model_snapshot_settle", "upstream-model-snapshot-settle", 0.467914, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_model_snapshot_settle", nil)
	req.Header.Set("Authorization", "Bearer sk-model-snapshot-settle")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"actual_cost_usd":0.007`) {
		t.Fatalf("settlement should use task model snapshot, body=%s", body)
	}
	if strings.Contains(string(body), `"actual_cost_usd":0.0009`) {
		t.Fatalf("settlement used upstream echo model: %s", body)
	}
}

func TestGetVideoRefundsHeldBalanceWhenTaskFails(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "failed"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-get-failed", "u_get_failed", "", 9.4, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_failed", "u_get_failed", "upstream-failed", 0.6, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_failed", nil)
	req.Header.Set("Authorization", "Bearer sk-get-failed")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"status":"failed"`) {
		t.Fatalf("body missing failed status: %s", body)
	}
	balance, err := db.TestUserBalance("u_get_failed")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 10 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestGetVideoUpdatesRunningTaskWithoutSettlement(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"status": "running"})
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-get-running", "u_get_running", "", 9.4, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_running", "u_get_running", "upstream-running", 0.6, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_running", nil)
	req.Header.Set("Authorization", "Bearer sk-get-running")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"status":"running"`) {
		t.Fatalf("body missing running status: %s", body)
	}
	balance, err := db.TestUserBalance("u_get_running")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.4 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestListVideosReturnsOnlyCustomerTasksNewestFirst(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-list", "u_list", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestUser("sk-other-list", "u_other_list", ""); err != nil {
		t.Fatalf("insert other user: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_old", "u_list", "queued", 100); err != nil {
		t.Fatalf("insert old task: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_new", "u_list", "succeeded", 200); err != nil {
		t.Fatalf("insert new task: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_other", "u_other_list", "succeeded", 300); err != nil {
		t.Fatalf("insert other task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos?limit=10", nil)
	req.Header.Set("Authorization", "Bearer sk-list")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("list videos: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"total":2`) {
		t.Fatalf("body missing total: %s", body)
	}
	if strings.Contains(string(body), "vid_other") {
		t.Fatalf("body leaked another user's task: %s", body)
	}
	newIndex := strings.Index(string(body), "vid_new")
	oldIndex := strings.Index(string(body), "vid_old")
	if newIndex < 0 || oldIndex < 0 || newIndex > oldIndex {
		t.Fatalf("tasks not newest first: %s", body)
	}
	if !strings.Contains(string(body), `"video_url":"https://media.example.test/v1/videos/vid_new/content"`) {
		t.Fatalf("succeeded task missing relay video_url: %s", body)
	}
	if strings.Contains(string(body), "byteplus.example.test") {
		t.Fatalf("list response leaked upstream URL: %s", body)
	}
}

func TestListVideosSupportsStatusLimitAndOffset(t *testing.T) {
	server, db, _ := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-list-filter", "u_list_filter", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_success_1", "u_list_filter", "succeeded", 100); err != nil {
		t.Fatalf("insert success 1: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_running", "u_list_filter", "running", 200); err != nil {
		t.Fatalf("insert running: %v", err)
	}
	if err := db.InsertTestTaskSummary("vid_success_2", "u_list_filter", "succeeded", 300); err != nil {
		t.Fatalf("insert success 2: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos?status=succeeded&limit=1&offset=1", nil)
	req.Header.Set("Authorization", "Bearer sk-list-filter")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("list videos: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), `"total":3`) {
		t.Fatalf("body missing total of all user tasks: %s", body)
	}
	if !strings.Contains(string(body), `"limit":1`) || !strings.Contains(string(body), `"offset":1`) {
		t.Fatalf("body missing pagination: %s", body)
	}
	if !strings.Contains(string(body), "vid_success_1") {
		t.Fatalf("body missing offset success task: %s", body)
	}
	if strings.Contains(string(body), "vid_success_2") || strings.Contains(string(body), "vid_running") {
		t.Fatalf("body includes filtered/paged-out task: %s", body)
	}
}

func TestVideoContentForwardsRangeAndHidesUpstreamURL(t *testing.T) {
	var gotRange string
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		gotRange = r.Header.Get("Range")
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", "3")
		w.Header().Set("Content-Range", "bytes 3-5/10")
		w.Header().Set("Accept-Ranges", "bytes")
		w.WriteHeader(http.StatusPartialContent)
		_, _ = w.Write([]byte("345"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-range", "u_range", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestTask("vid_range", "u_range", upstreamURL+"/upstream-video.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_range/content", nil)
	req.Header.Set("Authorization", "Bearer sk-range")
	req.Header.Set("Range", "bytes=3-5")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusPartialContent {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if string(body) != "345" {
		t.Fatalf("body = %q", body)
	}
	if gotRange != "bytes=3-5" {
		t.Fatalf("range = %q", gotRange)
	}
	if resp.Header.Get("Content-Range") != "bytes 3-5/10" {
		t.Fatalf("content-range = %q", resp.Header.Get("Content-Range"))
	}
	if resp.Header.Get("Location") != "" {
		t.Fatalf("content proxy must not redirect to upstream: %q", resp.Header.Get("Location"))
	}
	for key, values := range resp.Header {
		for _, value := range values {
			if strings.Contains(value, upstreamURL) {
				t.Fatalf("header %s leaked upstream URL %q", key, value)
			}
		}
	}
	if strings.Contains(string(body), "byteplus") {
		t.Fatalf("response leaked upstream url: %s", body)
	}
}

func TestVideoContentRefreshesQueuedTaskBeforeReturningNotReady(t *testing.T) {
	var upstreamURL string
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/contents/generations/tasks/upstream-content-ready":
			writeJSON(w, http.StatusOK, map[string]any{
				"status":     "succeeded",
				"model":      "dreamina-seedance-2-0-260128",
				"resolution": "480p",
				"usage": map[string]any{
					"completion_tokens": 1000,
				},
				"content": map[string]any{
					"video_url": upstreamURL + "/upstream-ready.mp4",
				},
			})
		case "/upstream-ready.mp4":
			w.Header().Set("Content-Type", "video/mp4")
			_, _ = w.Write([]byte("ready-video"))
		default:
			t.Fatalf("unexpected upstream path = %s", r.URL.Path)
		}
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-content-refresh", "u_content_refresh", "", 9.4, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_content_refresh", "u_content_refresh", "upstream-content-ready", 0.6, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_content_refresh/content", nil)
	req.Header.Set("Authorization", "Bearer sk-content-refresh")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("get content: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if string(body) != "ready-video" {
		t.Fatalf("body = %s", body)
	}
	if resp.Header.Get("Location") != "" {
		t.Fatalf("location leaked: %q", resp.Header.Get("Location"))
	}
	balance, err := db.TestUserBalance("u_content_refresh")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.993 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestVideoContentHeadRefreshesQueuedTaskBeforeReturningNotReady(t *testing.T) {
	var upstreamURL string
	var gotMethod, gotRange string
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/contents/generations/tasks/upstream-content-head-ready":
			writeJSON(w, http.StatusOK, map[string]any{
				"status":     "succeeded",
				"model":      "dreamina-seedance-2-0-260128",
				"resolution": "480p",
				"usage": map[string]any{
					"completion_tokens": 1000,
				},
				"content": map[string]any{
					"video_url": upstreamURL + "/upstream-head-ready.mp4",
				},
			})
		case "/upstream-head-ready.mp4":
			gotMethod = r.Method
			gotRange = r.Header.Get("Range")
			w.Header().Set("Content-Type", "video/mp4")
			w.Header().Set("Content-Length", "1")
			w.Header().Set("Content-Range", "bytes 0-0/11")
			w.WriteHeader(http.StatusPartialContent)
		default:
			t.Fatalf("unexpected upstream path = %s", r.URL.Path)
		}
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-content-head-refresh", "u_content_head_refresh", "", 9.4, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_content_head_refresh", "u_content_head_refresh", "upstream-content-head-ready", 0.6, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodHead, server.URL+"/v1/videos/vid_content_head_refresh/content", nil)
	req.Header.Set("Authorization", "Bearer sk-content-head-refresh")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("head content: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusPartialContent {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if len(body) != 0 {
		t.Fatalf("head returned body: %q", body)
	}
	if gotMethod != http.MethodGet {
		t.Fatalf("upstream content method = %q", gotMethod)
	}
	if gotRange != "bytes=0-0" {
		t.Fatalf("upstream content range = %q", gotRange)
	}
	if resp.Header.Get("Location") != "" {
		t.Fatalf("location leaked: %q", resp.Header.Get("Location"))
	}
	balance, err := db.TestUserBalance("u_content_head_refresh")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.993 {
		t.Fatalf("balance = %v", balance)
	}
}

func TestVideoContentRejectsRangeWhenUpstreamDoesNotReturnPartialContent(t *testing.T) {
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Range") != "bytes=3-5" {
			t.Fatalf("range = %q", r.Header.Get("Range"))
		}
		w.Header().Set("Content-Type", "video/mp4")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("full-body"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-range-200", "u_range_200", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestTask("vid_range_200", "u_range_200", upstreamURL+"/upstream-video.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_range_200/content", nil)
	req.Header.Set("Authorization", "Bearer sk-range-200")
	req.Header.Set("Range", "bytes=3-5")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "proxy_range_unsupported") {
		t.Fatalf("body missing proxy_range_unsupported: %s", body)
	}
	if resp.Header.Get("Content-Range") != "" {
		t.Fatalf("content-range should not be set on rejected range: %q", resp.Header.Get("Content-Range"))
	}
	if strings.Contains(string(body), upstreamURL) {
		t.Fatalf("response leaked upstream url: %s", body)
	}
}

func TestVideoContentRejectsUpstreamErrorWithoutLeakingBody(t *testing.T) {
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.Header().Set("Location", "https://byteplus.example.test/private.mp4")
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte("forbidden https://byteplus.example.test/private.mp4"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-video-error", "u_video_error", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestTask("vid_video_error", "u_video_error", upstreamURL+"/private.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_video_error/content", nil)
	req.Header.Set("Authorization", "Bearer sk-video-error")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if !strings.Contains(string(body), "proxy_error") {
		t.Fatalf("body missing proxy_error: %s", body)
	}
	if resp.Header.Get("Location") != "" {
		t.Fatalf("location leaked: %q", resp.Header.Get("Location"))
	}
	if strings.Contains(strings.ToLower(string(body)), "byteplus") || strings.Contains(string(body), upstreamURL) {
		t.Fatalf("response leaked upstream detail: %s", body)
	}
}

func TestVideoContentRejectsAnotherUsersTaskBeforeUpstream(t *testing.T) {
	upstreamCalled := false
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		upstreamCalled = true
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("should not stream"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-owner-video", "u_owner_video", ""); err != nil {
		t.Fatalf("insert owner: %v", err)
	}
	if err := db.InsertTestUser("sk-other-video", "u_other_video", ""); err != nil {
		t.Fatalf("insert other: %v", err)
	}
	if err := db.InsertTestTask("vid_private", "u_owner_video", upstreamURL+"/private.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_private/content", nil)
	req.Header.Set("Authorization", "Bearer sk-other-video")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if upstreamCalled {
		t.Fatalf("content proxy called upstream for another user's task")
	}
}

func TestVideoContentAcceptsRelaySessionCookie(t *testing.T) {
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "video/mp4")
		_, _ = w.Write([]byte("ok"))
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-cookie-video", "u_cookie_video", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestSession("sess_video", "u_cookie_video"); err != nil {
		t.Fatalf("insert session: %v", err)
	}
	if err := db.InsertTestTask("vid_cookie", "u_cookie_video", upstreamURL+"/upstream-video.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/videos/vid_cookie/content", nil)
	req.AddCookie(&http.Cookie{Name: "relay_session", Value: "sess_video"})
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("request video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d body=%s", resp.StatusCode, body)
	}
	if string(body) != "ok" {
		t.Fatalf("body = %q", body)
	}
}

func TestVideoContentHeadForwardsRangeWithoutBody(t *testing.T) {
	var gotMethod, gotRange string
	server, db, upstreamURL := newTestServer(t, func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotRange = r.Header.Get("Range")
		w.Header().Set("Content-Type", "video/mp4")
		w.Header().Set("Content-Length", "3")
		w.Header().Set("Content-Range", "bytes 3-5/10")
		w.WriteHeader(http.StatusPartialContent)
	})
	defer server.Close()
	defer db.Close()

	if err := db.InsertTestUser("sk-head", "u_head", ""); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestTask("vid_head", "u_head", upstreamURL+"/upstream-video.mp4"); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	req, _ := http.NewRequest(http.MethodHead, server.URL+"/v1/videos/vid_head/content", nil)
	req.Header.Set("Authorization", "Bearer sk-head")
	req.Header.Set("Range", "bytes=3-5")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("head video: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusPartialContent {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if len(body) != 0 {
		t.Fatalf("head returned body: %q", body)
	}
	if gotMethod != http.MethodGet {
		t.Fatalf("method = %q", gotMethod)
	}
	if gotRange != "bytes=3-5" {
		t.Fatalf("range = %q", gotRange)
	}
}
