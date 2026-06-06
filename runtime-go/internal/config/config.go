package config

import "os"

type Config struct {
	DBPath               string
	ListenAddr           string
	PublicDomain         string
	PublicBaseURL        string
	UpstreamBaseURL      string
	UpstreamAPIKey       string
	UpstreamAuthMode     string
	ControlPlaneBaseURL  string
	RuntimeInternalToken string
}

func Load() Config {
	return Config{
		DBPath:               env("DB_PATH", "/data/relay.sqlite"),
		ListenAddr:           env("RUNTIME_ADDR", "127.0.0.1:8012"),
		PublicDomain:         env("PUBLIC_DOMAIN", "video.example.com"),
		PublicBaseURL:        env("PUBLIC_BASE_URL", ""),
		UpstreamBaseURL:      env("UPSTREAM_BASE_URL", "https://ark.ap-southeast.bytepluses.com/api/v3"),
		UpstreamAPIKey:       env("UPSTREAM_API_KEY", ""),
		UpstreamAuthMode:     env("UPSTREAM_AUTH_MODE", "api_key"),
		ControlPlaneBaseURL:  env("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8002"),
		RuntimeInternalToken: env("RUNTIME_INTERNAL_TOKEN", ""),
	}
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
