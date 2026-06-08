package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"seedance-runtime/internal/config"
	"seedance-runtime/internal/models"
	"seedance-runtime/internal/pricing"
	"seedance-runtime/internal/store"
)

type Server struct {
	cfg    config.Config
	db     *store.DB
	client *http.Client
}

var errInvalidAuth = errors.New("invalid auth credentials")

func NewServer(cfg config.Config, db *store.DB, client *http.Client) *Server {
	if client == nil {
		client = http.DefaultClient
	}
	return &Server{cfg: cfg, db: db, client: client}
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", s.health)
	mux.HandleFunc("/v1/models", s.models)
	mux.HandleFunc("/v1/videos/estimate", s.estimateVideo)
	mux.HandleFunc("/v1/videos", s.createVideo)
	mux.HandleFunc("/v1/videos/", s.videoContent)
	return mux
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "service": "seedance-runtime"})
}

func (s *Server) models(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w)
		return
	}
	user, err := s.optionalAuth(r)
	if err != nil {
		if errors.Is(err, errInvalidAuth) {
			writeJSON(w, http.StatusUnauthorized, errorBody("missing_auth", "Authentication required"))
			return
		}
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	if user == nil {
		writeJSON(w, http.StatusOK, map[string]any{"data": models.Filter(nil)})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": models.Filter(modelAccess(user))})
}

func (s *Server) videoContent(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet && !strings.HasSuffix(r.URL.Path, "/content") {
		s.getVideo(w, r)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w)
		return
	}
	if !strings.HasSuffix(r.URL.Path, "/content") {
		notFound(w)
		return
	}
	taskID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/v1/videos/"), "/content")
	if taskID == "" || strings.Contains(taskID, "/") {
		notFound(w)
		return
	}
	user, ok := s.requiredAuth(w, r)
	if !ok {
		return
	}
	task, err := s.db.TaskForUser(taskID, user.ID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	if task == nil {
		writeJSON(w, http.StatusNotFound, errorBody("not_found", "video not found"))
		return
	}
	if !task.Settled {
		refreshed, err := s.refreshTask(r, user, task)
		if err == nil && refreshed != nil {
			task = refreshed
		}
	}
	if task.Status != "succeeded" {
		writeJSON(w, http.StatusConflict, errorBody("not_ready", "video is not ready"))
		return
	}
	if !task.CachedVideoURL.Valid || task.CachedVideoURL.String == "" {
		writeJSON(w, http.StatusNotFound, errorBody("video_unavailable", "video URL no longer available"))
		return
	}
	s.proxyVideo(w, r, taskID, task.CachedVideoURL.String)
}

type upstreamTaskResponse struct {
	Status     string `json:"status"`
	Model      string `json:"model"`
	Resolution string `json:"resolution"`
	Usage      struct {
		CompletionTokens int64 `json:"completion_tokens"`
	} `json:"usage"`
	Content struct {
		VideoURL string `json:"video_url"`
	} `json:"content"`
}

type estimateRequest struct {
	Model         string         `json:"model"`
	Content       []contentBlock `json:"content"`
	Resolution    string         `json:"resolution"`
	Ratio         string         `json:"ratio"`
	Duration      int            `json:"duration"`
	Frames        *int           `json:"frames"`
	GenerateAudio *bool          `json:"generate_audio"`
}

type contentBlock struct {
	Type string `json:"type"`
	Role string `json:"role"`
}

type createVideoRequest struct {
	Model         string           `json:"model"`
	Content       []map[string]any `json:"content"`
	Resolution    string           `json:"resolution"`
	Ratio         string           `json:"ratio"`
	Duration      int              `json:"duration"`
	Seed          *int             `json:"seed"`
	Watermark     *bool            `json:"watermark"`
	GenerateAudio *bool            `json:"generate_audio"`
	ExtraBody     map[string]any   `json:"extra_body"`
}

type customerNote struct {
	BytePlusEndpointID string `json:"byteplus_endpoint_id"`
}

var allowedContentBlockTypes = map[string]bool{
	"text":      true,
	"image_url": true,
	"video_url": true,
	"audio_url": true,
}

type contentBlockLimit struct {
	code  string
	limit int
}

var contentBlockLimits = map[string]contentBlockLimit{
	"image_url": {code: "too_many_reference_images", limit: 9},
	"video_url": {code: "too_many_reference_videos", limit: 3},
	"audio_url": {code: "too_many_reference_audios", limit: 3},
}

var allowedContentRoles = map[string]bool{
	"first_frame":     true,
	"last_frame":      true,
	"reference_image": true,
	"reference_video": true,
	"reference_audio": true,
}

func requiresVisualReference(model models.Model) bool {
	return model.UpstreamID == "seedance-1-0-lite-i2v-250428"
}

func (s *Server) estimateVideo(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w)
		return
	}
	user, ok := s.requiredAuth(w, r)
	if !ok {
		return
	}
	var req estimateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_json", "Invalid JSON request body"))
		return
	}
	model, ok := models.Lookup(req.Model)
	if !ok {
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_model", "Unknown model"))
		return
	}
	if !models.Enabled(req.Model, modelAccess(user)) {
		writeJSON(w, http.StatusForbidden, errorBodyWithFields(
			"model_not_enabled",
			"This model is not enabled for this customer",
			map[string]any{"model": req.Model},
		))
		return
	}
	if code, message := validateEstimateContent(req.Content); code != "" {
		writeJSON(w, http.StatusBadRequest, errorBody(code, message))
		return
	}
	if requiresVisualReference(model) && !hasEstimateVisualReference(req.Content) {
		writeJSON(w, http.StatusBadRequest, errorBodyWithFields(
			"visual_reference_required",
			"This model requires at least one image_url or video_url content block",
			map[string]any{"model": req.Model},
		))
		return
	}
	resolution := first(req.Resolution, "720p")
	ratio := first(req.Ratio, "16:9")
	duration := req.Duration
	if duration == 0 && req.Frames == nil {
		duration = 5
	}
	generateAudio := req.GenerateAudio != nil && *req.GenerateAudio
	if code, message := validateModelParameters(model, resolution, ratio, duration, generateAudio); code != "" {
		writeJSON(w, http.StatusBadRequest, errorBody(code, message))
		return
	}
	hasVideoRef := false
	for _, block := range req.Content {
		if block.Type == "video_url" {
			hasVideoRef = true
			break
		}
	}

	estimate := pricing.EstimateVideo(model.UpstreamID, resolution, duration, req.Frames, hasVideoRef, generateAudio)
	priceMultiplier := user.PriceMultiplier
	estimatedCost := round6(estimate.EstimatedCostUSD * priceMultiplier)
	maxCost := round6(estimate.MaxCostUSD * priceMultiplier)
	shortage := round6(maxCost - user.BalanceUSD)
	if shortage < 0 {
		shortage = 0
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"model":                       req.Model,
		"resolution":                  resolution,
		"ratio":                       ratio,
		"duration":                    duration,
		"frames":                      req.Frames,
		"has_video_ref":               hasVideoRef,
		"generate_audio":              generateAudio,
		"estimated_tokens":            estimate.EstimatedTokens,
		"estimated_cost_usd":          estimatedCost,
		"max_cost_usd":                maxCost,
		"upstream_estimated_cost_usd": estimate.EstimatedCostUSD,
		"upstream_max_cost_usd":       estimate.MaxCostUSD,
		"price_multiplier":            priceMultiplier,
		"pricing_scope":               "customer",
		"balance_usd":                 user.BalanceUSD,
		"can_afford":                  shortage == 0,
		"shortage_usd":                shortage,
	})
}

func (s *Server) createVideo(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		s.listVideos(w, r)
		return
	}
	if r.Method != http.MethodPost {
		methodNotAllowed(w)
		return
	}
	if s.rejectCookieWriteCSRF(w, r) {
		return
	}
	user, ok := s.requiredAuth(w, r)
	if !ok {
		return
	}
	var req createVideoRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_json", "Invalid JSON request body"))
		return
	}
	if len(req.Content) == 0 {
		writeJSON(w, http.StatusBadRequest, errorBody("missing_content", "Provide BytePlus-native content[] blocks"))
		return
	}
	if code, message := validateCreateContent(req.Content); code != "" {
		writeJSON(w, http.StatusBadRequest, errorBody(code, message))
		return
	}
	model, ok := models.Lookup(req.Model)
	if !ok {
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_model", "Unknown model"))
		return
	}
	if !models.Enabled(req.Model, modelAccess(user)) {
		writeJSON(w, http.StatusForbidden, errorBodyWithFields(
			"model_not_enabled",
			"This model is not enabled for this customer",
			map[string]any{"model": req.Model},
		))
		return
	}
	if requiresVisualReference(model) && !hasCreateVisualReference(req.Content) {
		writeJSON(w, http.StatusBadRequest, errorBodyWithFields(
			"visual_reference_required",
			"This model requires at least one image_url or video_url content block",
			map[string]any{"model": req.Model},
		))
		return
	}
	resolution := first(req.Resolution, "720p")
	ratio := first(req.Ratio, "16:9")
	duration := req.Duration
	if duration == 0 {
		duration = 5
	}
	generateAudio := req.GenerateAudio != nil && *req.GenerateAudio
	if code, message := validateModelParameters(model, resolution, ratio, duration, generateAudio); code != "" {
		writeJSON(w, http.StatusBadRequest, errorBody(code, message))
		return
	}
	hasVideoRef := hasVideoReference(req.Content)
	estimate := pricing.EstimateVideo(model.UpstreamID, resolution, duration, nil, hasVideoRef, generateAudio)
	priceMultiplier := user.PriceMultiplier
	estimatedCost := round6(estimate.EstimatedCostUSD * priceMultiplier)
	hold := round6(estimate.MaxCostUSD * priceMultiplier)
	if user.BalanceUSD < hold {
		writeJSON(w, http.StatusPaymentRequired, errorBodyWithFields(
			"insufficient_balance",
			"This request needs a larger reserved balance",
			map[string]any{"needed_usd": hold, "balance_usd": user.BalanceUSD},
		))
		return
	}
	upstreamKey, upstreamModel := s.customerUpstream(user, model.UpstreamID)
	if upstreamKey == "" {
		writeJSON(w, http.StatusServiceUnavailable, errorBody("no_upstream_key", "Service not configured: contact administrator"))
		return
	}
	reserved, err := s.db.ReserveBalanceIfAvailable(user.ID, hold)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	if !reserved {
		writeJSON(w, http.StatusPaymentRequired, errorBodyWithFields(
			"insufficient_balance",
			"This request needs a larger reserved balance",
			map[string]any{"needed_usd": hold, "balance_usd": user.BalanceUSD},
		))
		return
	}
	refundReserved := func() {
		if reserved {
			_ = s.db.RefundReservedBalance(user.ID, hold)
			reserved = false
		}
	}
	preparedContent, ok := s.prepareVideoContent(w, r, user, req)
	if !ok {
		refundReserved()
		return
	}
	req.Content = preparedContent
	upstreamPayload := map[string]any{
		"model":      upstreamModel,
		"content":    req.Content,
		"resolution": resolution,
		"ratio":      ratio,
		"duration":   duration,
	}
	if req.Seed != nil {
		upstreamPayload["seed"] = *req.Seed
	}
	watermark := false
	if req.Watermark != nil {
		watermark = *req.Watermark
	}
	upstreamPayload["watermark"] = watermark
	if req.GenerateAudio != nil {
		upstreamPayload["generate_audio"] = generateAudio
	}
	payloadBytes, err := json.Marshal(upstreamPayload)
	if err != nil {
		refundReserved()
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_payload", "Invalid request payload"))
		return
	}
	upstreamReq, err := http.NewRequestWithContext(
		r.Context(),
		http.MethodPost,
		strings.TrimRight(s.cfg.UpstreamBaseURL, "/")+"/contents/generations/tasks",
		strings.NewReader(string(payloadBytes)),
	)
	if err != nil {
		refundReserved()
		writeJSON(w, http.StatusInternalServerError, errorBody("upstream_error", "invalid upstream URL"))
		return
	}
	upstreamReq.Header.Set("Content-Type", "application/json")
	upstreamReq.Header.Set("Authorization", "Bearer "+upstreamKey)
	upstreamResp, err := s.client.Do(upstreamReq)
	if err != nil {
		refundReserved()
		writeJSON(w, http.StatusBadGateway, errorBody("upstream_error", "upstream unavailable"))
		return
	}
	defer upstreamResp.Body.Close()
	if upstreamResp.StatusCode != http.StatusOK {
		refundReserved()
		extra := map[string]string{}
		if requestID := upstreamRequestID(upstreamResp.Header); requestID != "" {
			extra["request_id"] = requestID
		}
		writeJSON(w, http.StatusBadGateway, errorBodyWithMetadata("upstream_error", "upstream returned an error", extra))
		return
	}
	var upstreamBody struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(upstreamResp.Body).Decode(&upstreamBody); err != nil || upstreamBody.ID == "" {
		refundReserved()
		writeJSON(w, http.StatusBadGateway, errorBody("upstream_error", "no task id returned"))
		return
	}
	cancelCreatedUpstream := func() {
		s.cancelUpstreamTask(r, upstreamKey, upstreamBody.ID)
	}
	taskID, err := newTaskID()
	if err != nil {
		cancelCreatedUpstream()
		refundReserved()
		writeJSON(w, http.StatusInternalServerError, errorBody("id_error", "could not create task id"))
		return
	}
	now := time.Now().Unix()
	markupPct := 0.0
	if user.MarkupPct.Valid {
		markupPct = user.MarkupPct.Float64
	}
	if err := s.db.CreateTaskWithReservedBalance(store.CreateTaskParams{
		ID:               taskID,
		UserID:           user.ID,
		UpstreamTaskID:   upstreamBody.ID,
		UpstreamModel:    upstreamModel,
		ClientModel:      req.Model,
		Resolution:       resolution,
		Duration:         duration,
		HasVideoRef:      hasVideoRef,
		EstimatedCostUSD: estimatedCost,
		HeldUSD:          hold,
		MarkupPct:        markupPct,
		PriceMultiplier:  priceMultiplier,
		PromptText:       promptText(req.Content),
		RequestPayload:   truncate(string(payloadBytes), 5000),
		Now:              now,
	}); err != nil {
		cancelCreatedUpstream()
		refundReserved()
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	reserved = false
	writeJSON(w, http.StatusOK, map[string]any{
		"id":                          taskID,
		"model":                       req.Model,
		"status":                      "queued",
		"estimated_cost_usd":          estimatedCost,
		"upstream_estimated_cost_usd": estimate.EstimatedCostUSD,
		"price_multiplier":            priceMultiplier,
		"pricing_scope":               "customer",
		"held_usd":                    hold,
		"created_at":                  now,
	})
}

func (s *Server) listVideos(w http.ResponseWriter, r *http.Request) {
	user, ok := s.requiredAuth(w, r)
	if !ok {
		return
	}
	query := r.URL.Query()
	limit := parseInt(query.Get("limit"), 20)
	offset := parseInt(query.Get("offset"), 0)
	status := strings.TrimSpace(query.Get("status"))
	tasks, total, err := s.db.ListTasksForUser(user.ID, status, limit, offset)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	data := make([]map[string]any, 0, len(tasks))
	for _, task := range tasks {
		data = append(data, s.formatTask(task))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"data":   data,
		"total":  total,
		"limit":  normalizedLimit(limit),
		"offset": normalizedOffset(offset),
	})
}

func (s *Server) prepareVideoContent(w http.ResponseWriter, r *http.Request, user *store.User, req createVideoRequest) ([]map[string]any, bool) {
	if s.cfg.RuntimeInternalToken == "" {
		if realPersonMode(req.ExtraBody) {
			writeJSON(w, http.StatusConflict, errorBody("real_person_not_supported", "real_person_mode requires runtime control-plane delegation"))
			return nil, false
		}
		return req.Content, true
	}
	payloadBytes, err := json.Marshal(map[string]any{
		"user_id":    user.ID,
		"content":    req.Content,
		"extra_body": req.ExtraBody,
	})
	if err != nil {
		writeJSON(w, http.StatusBadRequest, errorBody("invalid_payload", "Invalid request payload"))
		return nil, false
	}
	prepareReq, err := http.NewRequestWithContext(
		r.Context(),
		http.MethodPost,
		strings.TrimRight(s.cfg.ControlPlaneBaseURL, "/")+"/internal/runtime/prepare-video-content",
		strings.NewReader(string(payloadBytes)),
	)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("prepare_error", "invalid control-plane URL"))
		return nil, false
	}
	prepareReq.Header.Set("Content-Type", "application/json")
	prepareReq.Header.Set("X-Runtime-Token", s.cfg.RuntimeInternalToken)
	resp, err := s.client.Do(prepareReq)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, errorBody("prepare_error", "control-plane prepare failed"))
		return nil, false
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		writeJSON(w, resp.StatusCode, errorBody("prepare_error", "control-plane rejected video content"))
		return nil, false
	}
	var prepared struct {
		Content []map[string]any `json:"content"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&prepared); err != nil {
		writeJSON(w, http.StatusBadGateway, errorBody("prepare_error", "invalid control-plane prepare response"))
		return nil, false
	}
	if len(prepared.Content) == 0 {
		writeJSON(w, http.StatusBadGateway, errorBody("prepare_error", "control-plane returned empty content"))
		return nil, false
	}
	return prepared.Content, true
}

func (s *Server) getVideo(w http.ResponseWriter, r *http.Request) {
	taskID := strings.TrimPrefix(r.URL.Path, "/v1/videos/")
	if taskID == "" || strings.Contains(taskID, "/") {
		notFound(w)
		return
	}
	user, ok := s.requiredAuth(w, r)
	if !ok {
		return
	}
	task, err := s.db.TaskForUser(taskID, user.ID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return
	}
	if task == nil {
		writeJSON(w, http.StatusNotFound, errorBody("not_found", "video not found"))
		return
	}
	if task.Settled {
		writeJSON(w, http.StatusOK, s.formatTask(task))
		return
	}
	refreshed, err := s.refreshTask(r, user, task)
	if err != nil {
		writeJSON(w, http.StatusOK, s.formatTask(task))
		return
	}
	writeJSON(w, http.StatusOK, s.formatTask(refreshed))
}

func (s *Server) refreshTask(r *http.Request, user *store.User, task *store.Task) (*store.Task, error) {
	upstreamKey, _ := s.customerUpstream(user, task.UpstreamModel)
	if upstreamKey == "" {
		return task, nil
	}
	upstreamReq, err := http.NewRequestWithContext(
		r.Context(),
		http.MethodGet,
		strings.TrimRight(s.cfg.UpstreamBaseURL, "/")+"/contents/generations/tasks/"+task.UpstreamTaskID,
		nil,
	)
	if err != nil {
		return task, err
	}
	upstreamReq.Header.Set("Authorization", "Bearer "+upstreamKey)
	resp, err := s.client.Do(upstreamReq)
	if err != nil {
		return task, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return task, nil
	}
	var upstream upstreamTaskResponse
	if err := json.NewDecoder(resp.Body).Decode(&upstream); err != nil {
		return task, err
	}
	newStatus := upstream.Status
	if newStatus == "" {
		newStatus = task.Status
	}
	now := time.Now().Unix()
	cachedURL := ""
	if task.CachedVideoURL.Valid {
		cachedURL = task.CachedVideoURL.String
	}
	cachedUntil := int64(0)
	if task.CachedVideoURLUntil.Valid {
		cachedUntil = task.CachedVideoURLUntil.Int64
	}
	if newStatus == "succeeded" && upstream.Content.VideoURL != "" {
		cachedURL = upstream.Content.VideoURL
		cachedUntil = now + 23*3600
	}
	if isTerminal(newStatus) && !task.Settled {
		completionTokens := upstream.Usage.CompletionTokens
		var completionPtr *int64
		if completionTokens > 0 {
			completionPtr = &completionTokens
		}
		upstreamCost := 0.0
		actualCost := 0.0
		if newStatus == "succeeded" {
			resolution := taskResolution(task)
			model := taskPricingModel(task, upstream.Model)
			upstreamCost = pricing.ActualVideoCost(model, resolution, completionTokens, task.HasVideoRef)
			actualCost = round6(upstreamCost * taskPriceMultiplier(task))
		}
		held := 0.0
		if task.HeldUSD.Valid {
			held = task.HeldUSD.Float64
		}
		refund := held - actualCost
		if refund < 0 {
			refund = 0
		}
		return s.db.ApplyTaskRefresh(store.RefreshTaskParams{
			ID:                    task.ID,
			UserID:                user.ID,
			Status:                newStatus,
			CachedVideoURL:        cachedURL,
			CachedVideoURLUntil:   cachedUntil,
			ActualCostUSD:         actualCost,
			UpstreamActualCostUSD: upstreamCost,
			CompletionTokens:      completionPtr,
			RefundUSD:             round6(refund),
			Settled:               true,
			Now:                   now,
		})
	}
	return s.db.UpdateTaskStatus(task.ID, user.ID, newStatus, cachedURL, cachedUntil, now)
}

func (s *Server) formatTask(task *store.Task) map[string]any {
	out := map[string]any{
		"id":         task.ID,
		"model":      task.ClientModel,
		"status":     task.Status,
		"created_at": task.CreatedAt,
		"updated_at": task.UpdatedAt,
	}
	if task.Resolution.Valid {
		out["resolution"] = task.Resolution.String
	}
	if task.Duration.Valid {
		out["duration"] = task.Duration.Int64
	}
	if task.CompletionTokens.Valid {
		out["completion_tokens"] = task.CompletionTokens.Int64
	}
	if task.EstimatedCostUSD.Valid {
		out["estimated_cost_usd"] = task.EstimatedCostUSD.Float64
	}
	if task.ActualCostUSD.Valid {
		out["actual_cost_usd"] = task.ActualCostUSD.Float64
	}
	if task.PromptText.Valid {
		out["prompt_text"] = task.PromptText.String
	}
	if task.Status == "succeeded" {
		baseURL := strings.TrimRight(s.cfg.PublicBaseURL, "/")
		if baseURL == "" {
			baseURL = "https://" + strings.TrimRight(s.cfg.PublicDomain, "/")
		}
		out["video_url"] = baseURL + "/v1/videos/" + task.ID + "/content"
	}
	return out
}

func (s *Server) cancelUpstreamTask(r *http.Request, upstreamKey, upstreamTaskID string) {
	if strings.TrimSpace(upstreamKey) == "" || strings.TrimSpace(upstreamTaskID) == "" {
		return
	}
	cancelReq, err := http.NewRequestWithContext(
		r.Context(),
		http.MethodDelete,
		strings.TrimRight(s.cfg.UpstreamBaseURL, "/")+"/contents/generations/tasks/"+url.PathEscape(upstreamTaskID),
		nil,
	)
	if err != nil {
		return
	}
	cancelReq.Header.Set("Authorization", "Bearer "+upstreamKey)
	resp, err := s.client.Do(cancelReq)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
}

func (s *Server) proxyVideo(w http.ResponseWriter, r *http.Request, taskID, upstreamURL string) {
	upstreamMethod := r.Method
	if r.Method == http.MethodHead {
		upstreamMethod = http.MethodGet
	}
	req, err := http.NewRequestWithContext(r.Context(), upstreamMethod, upstreamURL, nil)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, errorBody("proxy_error", "invalid upstream URL"))
		return
	}
	requestedRange := false
	if value := r.Header.Get("Range"); value != "" {
		requestedRange = true
		req.Header.Set("Range", value)
	} else if r.Method == http.MethodHead {
		req.Header.Set("Range", "bytes=0-0")
	}
	resp, err := s.client.Do(req)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, errorBody("proxy_error", "upstream video unavailable"))
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		_, _ = io.Copy(io.Discard, resp.Body)
		writeJSON(w, http.StatusBadGateway, errorBody("proxy_error", "upstream video unavailable"))
		return
	}
	if requestedRange && resp.StatusCode != http.StatusPartialContent {
		_, _ = io.Copy(io.Discard, resp.Body)
		writeJSON(w, http.StatusBadGateway, errorBody("proxy_range_unsupported", "upstream video did not return partial content"))
		return
	}

	status := http.StatusOK
	if resp.StatusCode == http.StatusPartialContent {
		status = http.StatusPartialContent
	}
	header := w.Header()
	header.Set("Content-Disposition", `inline; filename="`+taskID+`.mp4"`)
	header.Set("Cache-Control", "private, max-age=3600")
	header.Set("Accept-Ranges", first(resp.Header.Get("Accept-Ranges"), "bytes"))
	for _, key := range []string{"Content-Length", "Content-Range", "Content-Type"} {
		if value := resp.Header.Get(key); value != "" {
			header.Set(key, value)
		}
	}
	if header.Get("Content-Type") == "" {
		header.Set("Content-Type", "video/mp4")
	}
	w.WriteHeader(status)
	if r.Method == http.MethodHead {
		return
	}
	_, _ = io.Copy(w, resp.Body)
}

func (s *Server) requiredAuth(w http.ResponseWriter, r *http.Request) (*store.User, bool) {
	user, err := s.optionalAuth(r)
	if err != nil {
		if errors.Is(err, errInvalidAuth) {
			writeJSON(w, http.StatusUnauthorized, errorBody("missing_auth", "Authentication required"))
			return nil, false
		}
		writeJSON(w, http.StatusInternalServerError, errorBody("db_error", "database error"))
		return nil, false
	}
	if user == nil {
		writeJSON(w, http.StatusUnauthorized, errorBody("missing_auth", "Authentication required"))
		return nil, false
	}
	return user, true
}

func (s *Server) rejectCookieWriteCSRF(w http.ResponseWriter, r *http.Request) bool {
	auth := r.Header.Get("Authorization")
	if _, ok := bearerTokenFromAuthorization(auth); ok {
		return false
	}
	cookie, err := r.Cookie("relay_session")
	if err != nil || strings.TrimSpace(cookie.Value) == "" {
		return false
	}
	if sameOriginWriteAllowed(r, s.cfg.PublicDomain) {
		return false
	}
	writeJSON(w, http.StatusForbidden, errorBody("csrf_origin_mismatch", "Cookie-authenticated write requests require same-origin Origin or Referer"))
	return true
}

func bearerTokenFromAuthorization(auth string) (string, bool) {
	raw := strings.TrimSpace(auth)
	if !strings.HasPrefix(strings.ToLower(raw), "bearer ") {
		return "", false
	}
	token := strings.TrimSpace(raw[len("Bearer "):])
	if token == "" {
		return "", false
	}
	return token, true
}

func sameOriginWriteAllowed(r *http.Request, publicDomain string) bool {
	source := first(r.Header.Get("Origin"), r.Header.Get("Referer"))
	sourceHost, sourceScheme := originHostAndScheme(source)
	if sourceHost == "" || sourceScheme == "" {
		return false
	}
	if allowed := hostWithoutPort(publicDomain); allowed != "" && strings.EqualFold(sourceHost, allowed) {
		return sourceScheme == "https"
	}
	if allowed := hostWithoutPort(r.Host); allowed != "" && strings.EqualFold(sourceHost, allowed) {
		return sourceScheme == requestScheme(r)
	}
	return false
}

func originHostAndScheme(value string) (string, string) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", ""
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return "", ""
	}
	return hostWithoutPort(parsed.Host), strings.ToLower(parsed.Scheme)
}

func requestScheme(r *http.Request) string {
	forwarded := strings.TrimSpace(strings.Split(r.Header.Get("X-Forwarded-Proto"), ",")[0])
	if forwarded != "" {
		return strings.ToLower(forwarded)
	}
	if r.TLS != nil {
		return "https"
	}
	return "http"
}

func hostWithoutPort(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	if parsed, err := url.Parse(value); err == nil && parsed.Host != "" {
		value = parsed.Host
	}
	if host, _, err := net.SplitHostPort(value); err == nil {
		return strings.Trim(strings.ToLower(host), "[]")
	}
	if strings.Count(value, ":") == 1 {
		value = strings.Split(value, ":")[0]
	}
	return strings.Trim(strings.ToLower(value), "[]")
}

func (s *Server) optionalAuth(r *http.Request) (*store.User, error) {
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if apiKey, ok := bearerTokenFromAuthorization(auth); ok {
		user, err := s.db.UserByAPIKey(apiKey)
		if err != nil {
			return nil, err
		}
		if user == nil {
			return nil, errInvalidAuth
		}
		return user, nil
	}
	if auth != "" {
		return nil, errInvalidAuth
	}
	cookie, err := r.Cookie("relay_session")
	if err == nil && strings.TrimSpace(cookie.Value) != "" {
		user, err := s.db.UserBySessionToken(strings.TrimSpace(cookie.Value), time.Now().Unix())
		if err != nil {
			return nil, err
		}
		if user == nil {
			return nil, errInvalidAuth
		}
		return user, nil
	}
	return nil, nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func methodNotAllowed(w http.ResponseWriter) {
	writeJSON(w, http.StatusMethodNotAllowed, errorBody("method_not_allowed", "method not allowed"))
}

func notFound(w http.ResponseWriter) {
	writeJSON(w, http.StatusNotFound, errorBody("not_found", "not found"))
}

func errorBody(code, message string) map[string]any {
	return errorBodyWithFields(code, message, nil)
}

func errorBodyWithMetadata(code, message string, extra map[string]string) map[string]any {
	fields := map[string]any{}
	for key, value := range extra {
		if strings.TrimSpace(value) != "" {
			fields[key] = value
		}
	}
	return errorBodyWithFields(code, message, fields)
}

func errorBodyWithFields(code, message string, extra map[string]any) map[string]any {
	err := map[string]any{"code": code, "message": message}
	for key, value := range extra {
		if value != nil {
			err[key] = value
		}
	}
	return map[string]any{"detail": map[string]any{"error": err}}
}

func upstreamRequestID(headers http.Header) string {
	for _, key := range []string{"X-Request-Id", "X-Tt-Logid", "X-Tt-Trace-Id", "Request-Id"} {
		if value := strings.TrimSpace(headers.Get(key)); value != "" {
			if len(value) > 128 {
				value = value[:128]
			}
			return value
		}
	}
	return ""
}

func first(value, fallback string) string {
	if value != "" {
		return value
	}
	return fallback
}

func round6(value float64) float64 {
	return float64(int64(value*1_000_000+0.5)) / 1_000_000
}

func newTaskID() (string, error) {
	var bytes [8]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return "", err
	}
	return "vid_" + hex.EncodeToString(bytes[:]), nil
}

func realPersonMode(extra map[string]any) bool {
	value, ok := extra["real_person_mode"]
	if !ok {
		return false
	}
	enabled, ok := value.(bool)
	return ok && enabled
}

func validateEstimateContent(content []contentBlock) (string, string) {
	counts := map[string]int{}
	for _, block := range content {
		if !allowedContentBlockTypes[block.Type] {
			return "invalid_content_block", "Unsupported content block type '" + block.Type + "'"
		}
		if block.Role != "" && !allowedContentRoles[block.Role] {
			return "invalid_content_role", "Unsupported content role '" + block.Role + "'"
		}
		counts[block.Type]++
	}
	return validateContentCounts(counts)
}

func validateCreateContent(content []map[string]any) (string, string) {
	counts := map[string]int{}
	for _, block := range content {
		blockType, ok := block["type"].(string)
		if !ok || !allowedContentBlockTypes[blockType] {
			return "invalid_content_block", "Unsupported content block type '" + blockType + "'"
		}
		if role, ok := block["role"].(string); ok && role != "" && !allowedContentRoles[role] {
			return "invalid_content_role", "Unsupported content role '" + role + "'"
		}
		counts[blockType]++
	}
	return validateContentCounts(counts)
}

func validateContentCounts(counts map[string]int) (string, string) {
	for blockType, limit := range contentBlockLimits {
		if counts[blockType] > limit.limit {
			return limit.code, "Too many " + blockType + " content blocks; maximum is " + strconv.Itoa(limit.limit)
		}
	}
	return "", ""
}

func validateModelParameters(model models.Model, resolution, ratio string, duration int, generateAudio bool) (string, string) {
	if !model.SupportsResolution(resolution) {
		return "unsupported_resolution", "Resolution '" + resolution + "' is not supported by this model"
	}
	if !model.SupportsRatio(ratio) {
		return "unsupported_ratio", "Ratio '" + ratio + "' is not supported by this model"
	}
	if !model.SupportsDuration(duration) {
		return "unsupported_duration", "Duration '" + strconv.Itoa(duration) + "' is not supported by this model"
	}
	if generateAudio && !model.Capabilities.SupportsAudio {
		return "unsupported_audio", "This model does not support generate_audio"
	}
	return "", ""
}

func hasEstimateVisualReference(content []contentBlock) bool {
	for _, block := range content {
		if block.Type == "image_url" || block.Type == "video_url" {
			return true
		}
	}
	return false
}

func hasCreateVisualReference(content []map[string]any) bool {
	for _, block := range content {
		blockType, _ := block["type"].(string)
		if blockType == "image_url" || blockType == "video_url" {
			return true
		}
	}
	return false
}

func hasVideoReference(content []map[string]any) bool {
	for _, block := range content {
		if block["type"] == "video_url" {
			return true
		}
	}
	return false
}

func isTerminal(status string) bool {
	return status == "succeeded" || status == "failed" || status == "cancelled" || status == "expired"
}

func taskResolution(task *store.Task) string {
	if task.Resolution.Valid && task.Resolution.String != "" {
		return task.Resolution.String
	}
	return "720p"
}

func taskPriceMultiplier(task *store.Task) float64 {
	if task.PriceMultiplier.Valid {
		return task.PriceMultiplier.Float64
	}
	return 1.0
}

func taskPricingModel(task *store.Task, upstreamEchoModel string) string {
	if task != nil && strings.TrimSpace(task.ClientModel) != "" {
		if model, ok := models.Lookup(task.ClientModel); ok {
			return model.UpstreamID
		}
	}
	if task != nil && strings.TrimSpace(task.UpstreamModel) != "" {
		if model, ok := models.Lookup(task.UpstreamModel); ok {
			return model.UpstreamID
		}
		return strings.TrimSpace(task.UpstreamModel)
	}
	return strings.TrimSpace(upstreamEchoModel)
}

func modelAccess(user *store.User) []string {
	if user == nil || !user.EnabledModelsSet {
		return nil
	}
	return user.EnabledModels
}

func (s *Server) customerUpstream(user *store.User, fallbackModel string) (string, string) {
	upstreamKey := strings.TrimSpace(s.cfg.UpstreamAPIKey)
	upstreamModel := fallbackModel
	customerKey := ""
	if user != nil && user.BytePlusAPIKey.Valid {
		customerKey = strings.TrimSpace(user.BytePlusAPIKey.String)
	}
	if user != nil && user.Note.Valid && strings.TrimSpace(user.Note.String) != "" {
		var note customerNote
		if err := json.Unmarshal([]byte(user.Note.String), &note); err == nil {
			if endpointID := strings.TrimSpace(note.BytePlusEndpointID); endpointID != "" {
				upstreamModel = endpointID
				if customerKey != "" {
					upstreamKey = customerKey
				}
			}
		}
	}
	if upstreamModel == fallbackModel && customerKey != "" && !isEndpointAuthMode(s.cfg.UpstreamAuthMode) {
		upstreamKey = customerKey
	}
	return upstreamKey, upstreamModel
}

func isEndpointAuthMode(mode string) bool {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "iam", "aksk", "access_key", "endpoint", "endpoint_api_key":
		return true
	default:
		return false
	}
}

func parseInt(value string, fallback int) int {
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func normalizedLimit(value int) int {
	if value <= 0 {
		return 20
	}
	if value > 100 {
		return 100
	}
	return value
}

func normalizedOffset(value int) int {
	if value < 0 {
		return 0
	}
	return value
}

func promptText(content []map[string]any) string {
	for _, block := range content {
		if block["type"] == "text" {
			if text, ok := block["text"].(string); ok {
				return truncate(text, 500)
			}
		}
	}
	return ""
}

func truncate(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}
