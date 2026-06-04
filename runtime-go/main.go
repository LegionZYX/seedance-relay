package main

import (
	"log"
	"net"
	"net/http"
	"os"
	"time"

	"seedance-runtime/internal/config"
	"seedance-runtime/internal/httpapi"
	"seedance-runtime/internal/store"
)

func main() {
	cfg := config.Load()
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(runHealthcheck(cfg))
	}

	db, err := store.Open(cfg.DBPath)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	defer db.Close()

	server := httpapi.NewServer(cfg, db, http.DefaultClient)
	log.Printf("seedance runtime listening on %s", cfg.ListenAddr)
	if err := http.ListenAndServe(cfg.ListenAddr, server.Routes()); err != nil {
		log.Fatal(err)
	}
}

func runHealthcheck(cfg config.Config) int {
	_, port, err := net.SplitHostPort(cfg.ListenAddr)
	if err != nil || port == "" {
		port = "8012"
	}

	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://127.0.0.1:" + port + "/health")
	if err != nil {
		return 1
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
