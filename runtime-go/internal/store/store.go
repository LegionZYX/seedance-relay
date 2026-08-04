package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"math"
	"time"

	_ "modernc.org/sqlite"
)

type DB struct {
	sql *sql.DB
}

type User struct {
	ID               string
	APIKey           string
	Email            sql.NullString
	BalanceUSD       float64
	MarkupPct        sql.NullFloat64
	PriceMultiplier  float64
	IsActive         bool
	EnabledModels    []string
	EnabledModelsSet bool
	BytePlusAPIKey   sql.NullString
	Note             sql.NullString
}

type Task struct {
	ID                    string
	UserID                string
	UpstreamTaskID        string
	UpstreamModel         string
	ClientModel           string
	Resolution            sql.NullString
	Duration              sql.NullInt64
	HasVideoRef           bool
	Status                string
	EstimatedCostUSD      sql.NullFloat64
	HeldUSD               sql.NullFloat64
	ActualCostUSD         sql.NullFloat64
	UpstreamActualCostUSD sql.NullFloat64
	PriceMultiplier       sql.NullFloat64
	CompletionTokens      sql.NullInt64
	Settled               bool
	CachedVideoURL        sql.NullString
	CachedVideoURLUntil   sql.NullInt64
	LocalVideoPath        sql.NullString
	PromptText            sql.NullString
	CreatedAt             int64
	UpdatedAt             int64
}

type RequestLog struct {
	ID                string
	UserID            sql.NullString
	TaskID            sql.NullString
	Route             sql.NullString
	Action            sql.NullString
	Model             sql.NullString
	PromptText        sql.NullString
	RequestPayload    sql.NullString
	StatusCode        sql.NullInt64
	ErrorCode         sql.NullString
	UpstreamRequestID sql.NullString
	IP                sql.NullString
	UserAgent         sql.NullString
	CreatedAt         int64
}

type RefreshTaskParams struct {
	ID                    string
	UserID                string
	Status                string
	CachedVideoURL        string
	CachedVideoURLUntil   int64
	ActualCostUSD         float64
	UpstreamActualCostUSD float64
	CompletionTokens      *int64
	RefundUSD             float64
	Settled               bool
	Now                   int64
}

type RequestLogParams struct {
	ID                string
	UserID            string
	TaskID            string
	Route             string
	Action            string
	Model             string
	PromptText        string
	RequestPayload    string
	StatusCode        int
	ErrorCode         string
	UpstreamRequestID string
	IP                string
	UserAgent         string
	CreatedAt         int64
}

type CreateTaskParams struct {
	ID               string
	UserID           string
	UpstreamTaskID   string
	UpstreamModel    string
	ClientModel      string
	Resolution       string
	Duration         int
	HasVideoRef      bool
	EstimatedCostUSD float64
	HeldUSD          float64
	MarkupPct        float64
	PriceMultiplier  float64
	PromptText       string
	RequestPayload   string
	Now              int64
}

func Open(path string) (*DB, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec("PRAGMA busy_timeout=5000"); err != nil {
		_ = db.Close()
		return nil, err
	}
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, err
	}
	for _, migration := range migrations {
		_, _ = db.Exec(migration)
	}
	return &DB{sql: db}, nil
}

const schema = `
CREATE TABLE IF NOT EXISTS users (
    id                       TEXT PRIMARY KEY,
    api_key                  TEXT UNIQUE NOT NULL,
    email                    TEXT UNIQUE,
    balance_usd              REAL NOT NULL DEFAULT 0,
    markup_pct               REAL,
    price_multiplier         REAL NOT NULL DEFAULT 1.0,
    enabled_models           TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1,
    is_admin                 INTEGER NOT NULL DEFAULT 0,
    password_hash            TEXT,
    byteplus_api_key         TEXT,
    byteplus_account_label   TEXT,
    note                     TEXT,
    api_key_last_rotated_at  INTEGER,
    password_changed_at      INTEGER,
    failed_login_count       INTEGER NOT NULL DEFAULT 0,
    locked_until             INTEGER,
    created_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    upstream_task_id         TEXT UNIQUE NOT NULL,
    upstream_model           TEXT NOT NULL,
    client_model             TEXT NOT NULL,
    resolution               TEXT,
    duration                 INTEGER,
    has_video_ref            INTEGER DEFAULT 0,
    status                   TEXT NOT NULL DEFAULT 'queued',
    estimated_cost_usd       REAL,
    held_usd                 REAL,
    actual_cost_usd          REAL,
    upstream_actual_cost_usd REAL,
    markup_pct               REAL,
    price_multiplier         REAL,
    completion_tokens        INTEGER,
    settled                  INTEGER NOT NULL DEFAULT 0,
    cached_video_url         TEXT,
    cached_video_url_until   INTEGER,
    local_video_path         TEXT,
    prompt_text              TEXT,
    request_payload          TEXT,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token                    TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,
    expires_at               INTEGER NOT NULL,
    created_at               INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS request_logs (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT,
    task_id             TEXT,
    route               TEXT,
    action              TEXT,
    model               TEXT,
    prompt_text         TEXT,
    request_payload     TEXT,
    status_code         INTEGER,
    error_code          TEXT,
    upstream_request_id TEXT,
    ip                  TEXT,
    user_agent          TEXT,
    created_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_request_logs_user ON request_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_task ON request_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_created ON request_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_request_logs_action ON request_logs(action);
`

var migrations = []string{
	"ALTER TABLE users ADD COLUMN price_multiplier REAL NOT NULL DEFAULT 1.0",
	"ALTER TABLE users ADD COLUMN enabled_models TEXT",
	"ALTER TABLE tasks ADD COLUMN price_multiplier REAL",
	`CREATE TABLE IF NOT EXISTS request_logs (
		id                  TEXT PRIMARY KEY,
		user_id             TEXT,
		task_id             TEXT,
		route               TEXT,
		action              TEXT,
		model               TEXT,
		prompt_text         TEXT,
		request_payload     TEXT,
		status_code         INTEGER,
		error_code          TEXT,
		upstream_request_id TEXT,
		ip                  TEXT,
		user_agent          TEXT,
		created_at          INTEGER NOT NULL
	)`,
	"CREATE INDEX IF NOT EXISTS idx_request_logs_user ON request_logs(user_id)",
	"CREATE INDEX IF NOT EXISTS idx_request_logs_task ON request_logs(task_id)",
	"CREATE INDEX IF NOT EXISTS idx_request_logs_created ON request_logs(created_at)",
	"CREATE INDEX IF NOT EXISTS idx_request_logs_action ON request_logs(action)",
}

func (db *DB) Close() error {
	return db.sql.Close()
}

func (db *DB) UserByAPIKey(apiKey string) (*User, error) {
	row := db.sql.QueryRow(`
		SELECT id, api_key, email, balance_usd, markup_pct, price_multiplier,
		       is_active, enabled_models, byteplus_api_key, note
		FROM users
		WHERE api_key = ? AND is_active = 1
	`, apiKey)
	var user User
	var enabled sql.NullString
	var active int
	if err := row.Scan(
		&user.ID, &user.APIKey, &user.Email, &user.BalanceUSD, &user.MarkupPct,
		&user.PriceMultiplier, &active, &enabled, &user.BytePlusAPIKey, &user.Note,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	user.IsActive = active == 1
	user.EnabledModels, user.EnabledModelsSet = parseEnabledModels(enabled)
	return &user, nil
}

func (db *DB) UserBySessionToken(token string, now int64) (*User, error) {
	row := db.sql.QueryRow(`
		SELECT u.id, u.api_key, u.email, u.balance_usd, u.markup_pct, u.price_multiplier,
		       u.is_active, u.enabled_models, u.byteplus_api_key, u.note, s.expires_at
		FROM sessions s
		JOIN users u ON s.user_id = u.id
		WHERE s.token = ?
	`, token)
	var user User
	var enabled sql.NullString
	var active int
	var expiresAt int64
	if err := row.Scan(
		&user.ID, &user.APIKey, &user.Email, &user.BalanceUSD, &user.MarkupPct,
		&user.PriceMultiplier, &active, &enabled, &user.BytePlusAPIKey, &user.Note, &expiresAt,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	if expiresAt < now || active != 1 {
		_, _ = db.sql.Exec("DELETE FROM sessions WHERE token = ?", token)
		return nil, nil
	}
	user.IsActive = true
	user.EnabledModels, user.EnabledModelsSet = parseEnabledModels(enabled)
	return &user, nil
}

func parseEnabledModels(raw sql.NullString) ([]string, bool) {
	if !raw.Valid || raw.String == "" {
		return nil, false
	}
	var items []string
	if err := json.Unmarshal([]byte(raw.String), &items); err != nil {
		return []string{}, true
	}
	return items, true
}

func (db *DB) TaskForUser(taskID, userID string) (*Task, error) {
	row := db.sql.QueryRow(`
		SELECT id, user_id, upstream_task_id, upstream_model, client_model,
		       resolution, duration, has_video_ref, status,
		       estimated_cost_usd, held_usd, actual_cost_usd, upstream_actual_cost_usd,
		       price_multiplier, completion_tokens, settled,
		       cached_video_url, cached_video_url_until, local_video_path,
		       prompt_text, created_at, updated_at
		FROM tasks
		WHERE id = ? AND user_id = ?
	`, taskID, userID)
	var task Task
	var hasVideoRef int
	var settled int
	if err := row.Scan(
		&task.ID, &task.UserID, &task.UpstreamTaskID, &task.UpstreamModel, &task.ClientModel,
		&task.Resolution, &task.Duration, &hasVideoRef, &task.Status,
		&task.EstimatedCostUSD, &task.HeldUSD, &task.ActualCostUSD, &task.UpstreamActualCostUSD,
		&task.PriceMultiplier, &task.CompletionTokens, &settled,
		&task.CachedVideoURL, &task.CachedVideoURLUntil, &task.LocalVideoPath,
		&task.PromptText, &task.CreatedAt, &task.UpdatedAt,
	); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	task.HasVideoRef = hasVideoRef == 1
	task.Settled = settled == 1
	return &task, nil
}

func (db *DB) ApplyTaskRefresh(params RefreshTaskParams) (*Task, error) {
	tx, err := db.sql.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	settled := 0
	if params.Settled {
		settled = 1
	}
	var completion any
	if params.CompletionTokens != nil {
		completion = *params.CompletionTokens
	}

	where := "WHERE id = ? AND user_id = ?"
	if params.Settled {
		where += " AND settled = 0"
	}
	result, err := tx.Exec(`
		UPDATE tasks
		SET status = ?, actual_cost_usd = ?, upstream_actual_cost_usd = ?,
		    completion_tokens = ?, settled = ?, cached_video_url = ?,
		    cached_video_url_until = ?, updated_at = ?
		`+where,
		params.Status, params.ActualCostUSD, params.UpstreamActualCostUSD,
		completion, settled, nullString(params.CachedVideoURL), nullInt64(params.CachedVideoURLUntil),
		params.Now, params.ID, params.UserID)
	if err != nil {
		return nil, err
	}
	rows, err := result.RowsAffected()
	if err != nil {
		return nil, err
	}
	if rows > 0 && params.RefundUSD > 0 {
		if _, err := tx.Exec("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", params.RefundUSD, params.UserID); err != nil {
			return nil, err
		}
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	return db.TaskForUser(params.ID, params.UserID)
}

func (db *DB) UpdateTaskStatus(taskID, userID, status, cachedURL string, cachedUntil, now int64) (*Task, error) {
	_, err := db.sql.Exec(`
		UPDATE tasks
		SET status = ?, cached_video_url = ?, cached_video_url_until = ?, updated_at = ?
		WHERE id = ? AND user_id = ?
	`, status, nullString(cachedURL), nullInt64(cachedUntil), now, taskID, userID)
	if err != nil {
		return nil, err
	}
	return db.TaskForUser(taskID, userID)
}

func (db *DB) ListTasksForUser(userID, status string, limit, offset int) ([]*Task, int, error) {
	if limit <= 0 {
		limit = 20
	}
	if limit > 100 {
		limit = 100
	}
	if offset < 0 {
		offset = 0
	}
	var rows *sql.Rows
	var err error
	if status != "" {
		rows, err = db.sql.Query(`
			SELECT id
			FROM tasks
			WHERE user_id = ? AND status = ?
			ORDER BY created_at DESC
			LIMIT ? OFFSET ?
		`, userID, status, limit, offset)
	} else {
		rows, err = db.sql.Query(`
			SELECT id
			FROM tasks
			WHERE user_id = ?
			ORDER BY created_at DESC
			LIMIT ? OFFSET ?
		`, userID, limit, offset)
	}
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	tasks := []*Task{}
	for rows.Next() {
		var taskID string
		if err := rows.Scan(&taskID); err != nil {
			return nil, 0, err
		}
		task, err := db.TaskForUser(taskID, userID)
		if err != nil {
			return nil, 0, err
		}
		if task != nil {
			tasks = append(tasks, task)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, 0, err
	}
	var total int
	if err := db.sql.QueryRow("SELECT COUNT(*) FROM tasks WHERE user_id = ?", userID).Scan(&total); err != nil {
		return nil, 0, err
	}
	return tasks, total, nil
}

func (db *DB) HoldBalanceAndCreateTask(params CreateTaskParams) error {
	tx, err := db.sql.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.Exec("UPDATE users SET balance_usd = balance_usd - ? WHERE id = ?", params.HeldUSD, params.UserID); err != nil {
		return err
	}
	hasVideoRef := 0
	if params.HasVideoRef {
		hasVideoRef = 1
	}
	_, err = tx.Exec(`
		INSERT INTO tasks
			(id, user_id, upstream_task_id, upstream_model, client_model,
			 resolution, duration, has_video_ref, status,
			 estimated_cost_usd, held_usd, markup_pct, price_multiplier,
			 prompt_text, request_payload, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
	`, params.ID, params.UserID, params.UpstreamTaskID, params.UpstreamModel, params.ClientModel,
		params.Resolution, params.Duration, hasVideoRef, params.EstimatedCostUSD, params.HeldUSD,
		params.MarkupPct, params.PriceMultiplier, params.PromptText, params.RequestPayload,
		params.Now, params.Now)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (db *DB) ReserveBalanceIfAvailable(userID string, amount float64) (bool, error) {
	tx, err := db.sql.Begin()
	if err != nil {
		return false, err
	}
	defer tx.Rollback()

	result, err := tx.Exec(
		"UPDATE users SET balance_usd = balance_usd - ? WHERE id = ? AND balance_usd >= ?",
		amount,
		userID,
		amount,
	)
	if err != nil {
		return false, err
	}
	rows, err := result.RowsAffected()
	if err != nil {
		return false, err
	}
	if rows == 0 {
		return false, nil
	}
	if err := tx.Commit(); err != nil {
		return false, err
	}
	return true, nil
}

func (db *DB) RefundReservedBalance(userID string, amount float64) error {
	_, err := db.sql.Exec("UPDATE users SET balance_usd = balance_usd + ? WHERE id = ?", amount, userID)
	return err
}

func (db *DB) CreateTaskWithReservedBalance(params CreateTaskParams) error {
	hasVideoRef := 0
	if params.HasVideoRef {
		hasVideoRef = 1
	}
	_, err := db.sql.Exec(`
		INSERT INTO tasks
			(id, user_id, upstream_task_id, upstream_model, client_model,
			 resolution, duration, has_video_ref, status,
			 estimated_cost_usd, held_usd, markup_pct, price_multiplier,
			 prompt_text, request_payload, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
	`, params.ID, params.UserID, params.UpstreamTaskID, params.UpstreamModel, params.ClientModel,
		params.Resolution, params.Duration, hasVideoRef, params.EstimatedCostUSD, params.HeldUSD,
		params.MarkupPct, params.PriceMultiplier, params.PromptText, params.RequestPayload,
		params.Now, params.Now)
	return err
}

func (db *DB) InsertRequestLog(params RequestLogParams) error {
	_, err := db.sql.Exec(`
		INSERT INTO request_logs
			(id, user_id, task_id, route, action, model, prompt_text,
			 request_payload, status_code, error_code, upstream_request_id,
			 ip, user_agent, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, params.ID, nullString(params.UserID), nullString(params.TaskID),
		nullString(params.Route), nullString(params.Action), nullString(params.Model),
		nullString(params.PromptText), nullString(params.RequestPayload), params.StatusCode,
		nullString(params.ErrorCode), nullString(params.UpstreamRequestID), nullString(params.IP),
		nullString(params.UserAgent), params.CreatedAt)
	return err
}

func (db *DB) InsertTestUser(apiKey, userID, enabledModels string) error {
	return db.InsertTestUserWithBalance(apiKey, userID, enabledModels, 100, 1.0)
}

func (db *DB) InsertTestUserWithBalance(apiKey, userID, enabledModels string, balanceUSD, priceMultiplier float64) error {
	return db.InsertTestUserWithUpstreamKey(apiKey, userID, enabledModels, balanceUSD, priceMultiplier, "")
}

func (db *DB) InsertTestUserWithUpstreamKey(apiKey, userID, enabledModels string, balanceUSD, priceMultiplier float64, bytePlusAPIKey string) error {
	_, err := db.sql.Exec(`
		INSERT INTO users
			(id, api_key, email, balance_usd, is_active, is_admin, enabled_models,
			 price_multiplier, byteplus_api_key, created_at)
		VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?, ?)
	`, userID, apiKey, userID+"@example.test", balanceUSD, enabledModels, priceMultiplier, bytePlusAPIKey, time.Now().Unix())
	return err
}

func (db *DB) SetTestUserNote(userID, note string) error {
	_, err := db.sql.Exec("UPDATE users SET note = ? WHERE id = ?", note, userID)
	return err
}

func (db *DB) TestUserBalance(userID string) (float64, error) {
	var balance float64
	err := db.sql.QueryRow("SELECT balance_usd FROM users WHERE id = ?", userID).Scan(&balance)
	return math.Round(balance*1_000_000) / 1_000_000, err
}

func (db *DB) SetTestUserPriceMultiplier(userID string, priceMultiplier float64) error {
	_, err := db.sql.Exec("UPDATE users SET price_multiplier = ? WHERE id = ?", priceMultiplier, userID)
	return err
}

func (db *DB) InsertTestSession(token, userID string) error {
	now := time.Now().Unix()
	_, err := db.sql.Exec(`
		INSERT INTO sessions (token, user_id, expires_at, created_at)
		VALUES (?, ?, ?, ?)
	`, token, userID, now+3600, now)
	return err
}

func (db *DB) InsertTestTask(taskID, userID, videoURL string) error {
	_, err := db.sql.Exec(`
		INSERT INTO tasks
			(id, user_id, upstream_task_id, upstream_model, client_model, status,
			 settled, cached_video_url, cached_video_url_until, created_at, updated_at)
		VALUES (?, ?, ?, 'dreamina-seedance-2-0-260128', 'dreamina-seedance-2-0-260128', 'succeeded',
			1, ?, 9999999999, ?, ?)
	`, taskID, userID, "upstream-"+taskID, videoURL, time.Now().Unix(), time.Now().Unix())
	return err
}

func (db *DB) InsertTestQueuedTask(taskID, userID, upstreamTaskID string, heldUSD, priceMultiplier float64) error {
	now := time.Now().Unix()
	_, err := db.sql.Exec(`
		INSERT INTO tasks
			(id, user_id, upstream_task_id, upstream_model, client_model,
			 resolution, duration, has_video_ref, status,
			 estimated_cost_usd, held_usd, price_multiplier, settled,
			 prompt_text, created_at, updated_at)
		VALUES (?, ?, ?, 'dreamina-seedance-2-0-260128', 'dreamina-seedance-2-0-260128',
			'480p', 5, 0, 'queued', ?, ?, ?, 0, 'A quiet street walk.', ?, ?)
	`, taskID, userID, upstreamTaskID, heldUSD, heldUSD, priceMultiplier, now, now)
	return err
}

func (db *DB) InsertTestTaskSummary(taskID, userID, status string, createdAt int64) error {
	settled := 0
	cachedURL := any(nil)
	cachedUntil := any(nil)
	actualCost := any(nil)
	if status == "succeeded" {
		settled = 1
		cachedURL = "https://byteplus.example.test/" + taskID + ".mp4"
		cachedUntil = int64(9999999999)
		actualCost = 0.01
	}
	_, err := db.sql.Exec(`
		INSERT INTO tasks
			(id, user_id, upstream_task_id, upstream_model, client_model,
			 resolution, duration, has_video_ref, status, estimated_cost_usd,
			 held_usd, actual_cost_usd, price_multiplier, settled,
			 cached_video_url, cached_video_url_until, prompt_text, created_at, updated_at)
		VALUES (?, ?, ?, 'dreamina-seedance-2-0-260128', 'dreamina-seedance-2-0-260128',
			'480p', 5, 0, ?, 0.5, 0.6, ?, 1.0, ?,
			?, ?, 'A quiet street walk.', ?, ?)
	`, taskID, userID, "upstream-"+taskID, status, actualCost, settled,
		cachedURL, cachedUntil, createdAt, createdAt)
	return err
}

func (db *DB) TestLatestRequestLog(userID, action string) (*RequestLog, error) {
	row := db.sql.QueryRow(`
		SELECT id, user_id, task_id, route, action, model, prompt_text,
		       request_payload, status_code, error_code, upstream_request_id,
		       ip, user_agent, created_at
		  FROM request_logs
		 WHERE user_id = ? AND action = ?
		 ORDER BY created_at DESC, id DESC
		 LIMIT 1
	`, userID, action)
	var log RequestLog
	if err := row.Scan(
		&log.ID, &log.UserID, &log.TaskID, &log.Route, &log.Action, &log.Model,
		&log.PromptText, &log.RequestPayload, &log.StatusCode, &log.ErrorCode,
		&log.UpstreamRequestID, &log.IP, &log.UserAgent, &log.CreatedAt,
	); err != nil {
		return nil, err
	}
	return &log, nil
}

func nullString(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func nullInt64(value int64) any {
	if value == 0 {
		return nil
	}
	return value
}
