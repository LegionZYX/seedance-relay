package store

import (
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOpenConfiguresSQLiteForAlpha1Boundary(t *testing.T) {
	db, err := Open(filepath.Join(t.TempDir(), "relay.sqlite"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	var journalMode string
	if err := db.sql.QueryRow("PRAGMA journal_mode").Scan(&journalMode); err != nil {
		t.Fatalf("journal mode: %v", err)
	}
	var busyTimeout int
	if err := db.sql.QueryRow("PRAGMA busy_timeout").Scan(&busyTimeout); err != nil {
		t.Fatalf("busy timeout: %v", err)
	}

	if strings.ToLower(journalMode) != "wal" {
		t.Fatalf("journal_mode = %q", journalMode)
	}
	if busyTimeout < 5000 {
		t.Fatalf("busy_timeout = %d", busyTimeout)
	}
}

func TestApplyTaskRefreshRefundsTerminalTaskOnlyOnce(t *testing.T) {
	db, err := Open(filepath.Join(t.TempDir(), "relay.sqlite"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	if err := db.InsertTestUserWithBalance("sk-settle-once", "u_settle_once", "", 9.4, 1.0); err != nil {
		t.Fatalf("insert user: %v", err)
	}
	if err := db.InsertTestQueuedTask("vid_settle_once", "u_settle_once", "upstream-settle-once", 0.6, 1.0); err != nil {
		t.Fatalf("insert task: %v", err)
	}

	completionTokens := int64(1000)
	params := RefreshTaskParams{
		ID:                    "vid_settle_once",
		UserID:                "u_settle_once",
		Status:                "succeeded",
		CachedVideoURL:        "https://provider.example.test/video.mp4",
		CachedVideoURLUntil:   time.Now().Unix() + 3600,
		ActualCostUSD:         0.1,
		UpstreamActualCostUSD: 0.1,
		CompletionTokens:      &completionTokens,
		RefundUSD:             0.5,
		Settled:               true,
		Now:                   time.Now().Unix(),
	}

	if _, err := db.ApplyTaskRefresh(params); err != nil {
		t.Fatalf("first refresh: %v", err)
	}
	if _, err := db.ApplyTaskRefresh(params); err != nil {
		t.Fatalf("second refresh: %v", err)
	}

	balance, err := db.TestUserBalance("u_settle_once")
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 9.9 {
		t.Fatalf("balance should include one refund only, got %v", balance)
	}
}
