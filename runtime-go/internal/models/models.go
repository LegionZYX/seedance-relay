package models

import (
	"encoding/json"
	"log"
	"os"
)

type DurationRange struct {
	Min int `json:"min"`
	Max int `json:"max"`
}

type Capabilities struct {
	SupportsAudio          bool `json:"supports_audio"`
	SupportsReferenceImage bool `json:"supports_reference_image"`
	SupportsReferenceVideo bool `json:"supports_reference_video"`
	SupportsReferenceAudio bool `json:"supports_reference_audio"`
}

type Model struct {
	ID                   string        `json:"id"`
	Description          string        `json:"description"`
	SupportedResolutions []string      `json:"supported_resolutions"`
	SupportedRatios      []string      `json:"supported_ratios"`
	DurationSeconds      DurationRange `json:"duration_seconds"`
	Capabilities         Capabilities  `json:"capabilities"`
	UpstreamID           string        `json:"-"`
}

var defaultResolutions = []string{"480p", "720p", "1080p"}
var defaultRatios = []string{"16:9", "9:16", "1:1"}
var defaultDuration = DurationRange{Min: 2, Max: 15}
var defaultCapabilities = Capabilities{
	SupportsAudio:          true,
	SupportsReferenceImage: true,
	SupportsReferenceVideo: true,
	SupportsReferenceAudio: true,
}

var nativeRegistry = []Model{
	newModel("dreamina-seedance-2-0-260128", "High quality video generation", "dreamina-seedance-2-0-260128"),
	newModel("dreamina-seedance-2-0-fast-260128", "Fast high quality video generation", "dreamina-seedance-2-0-fast-260128"),
	newModel("seedance-1-5-pro-251215", "Seedance 1.5 pro compatible model", "seedance-1-5-pro-251215"),
	newModel("seedance-1-0-pro-250528", "1080p video generation", "seedance-1-0-pro-250528"),
	newModel("seedance-1-0-pro-fast-251015", "720p video generation", "seedance-1-0-pro-fast-251015"),
	newModel("seedance-1-0-lite-t2v-250428", "Lite text-to-video generation", "seedance-1-0-lite-t2v-250428"),
	func() Model {
		model := newModel("seedance-1-0-lite-i2v-250428", "Lite image-to-video generation", "seedance-1-0-lite-i2v-250428")
		return model
	}(),
}

var registry = withAliases(nativeRegistry)

func withAliases(native []Model) []Model {
	out := make([]Model, len(native))
	copy(out, native)
	raw := os.Getenv("MODEL_ID_ALIASES_JSON")
	if raw == "" {
		return out
	}
	parsed := map[string]string{}
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		log.Printf("invalid MODEL_ID_ALIASES_JSON: %v", err)
		return out
	}
	byNativeID := map[string]Model{}
	for _, model := range native {
		byNativeID[model.ID] = model
	}
	for alias, upstreamID := range parsed {
		if alias == "" || upstreamID == "" {
			continue
		}
		if _, exists := byNativeID[alias]; exists {
			log.Printf("ignored MODEL_ID_ALIASES_JSON alias %q: conflicts with native model id", alias)
			continue
		}
		base, ok := byNativeID[upstreamID]
		if !ok {
			log.Printf("ignored MODEL_ID_ALIASES_JSON alias %q: unknown native model id %q", alias, upstreamID)
			continue
		}
		base.ID = alias
		base.UpstreamID = upstreamID
		out = append(out, base)
	}
	return out
}

func newModel(id, description, upstreamID string) Model {
	return Model{
		ID:                   id,
		Description:          description,
		SupportedResolutions: append([]string{}, defaultResolutions...),
		SupportedRatios:      append([]string{}, defaultRatios...),
		DurationSeconds:      defaultDuration,
		Capabilities:         defaultCapabilities,
		UpstreamID:           upstreamID,
	}
}

func All() []Model {
	out := make([]Model, len(nativeRegistry))
	copy(out, nativeRegistry)
	return out
}

func IDs() map[string]bool {
	out := map[string]bool{}
	for _, model := range registry {
		out[model.ID] = true
	}
	return out
}

func Lookup(id string) (Model, bool) {
	for _, model := range registry {
		if model.ID == id {
			return model, true
		}
	}
	return Model{}, false
}

func Enabled(id string, enabled []string) bool {
	if enabled == nil {
		for _, model := range nativeRegistry {
			if model.ID == id {
				return true
			}
		}
		return false
	}
	for _, item := range enabled {
		if item == id {
			_, ok := Lookup(id)
			return ok
		}
	}
	return false
}

func filterRegistry(enabled []string, registry []Model) []Model {
	if enabled == nil {
		out := make([]Model, len(nativeRegistry))
		copy(out, nativeRegistry)
		return out
	}
	byID := map[string]Model{}
	for _, model := range registry {
		byID[model.ID] = model
	}
	out := []Model{}
	seen := map[string]bool{}
	for _, id := range enabled {
		if seen[id] {
			continue
		}
		if model, ok := byID[id]; ok {
			out = append(out, model)
			seen[id] = true
		}
	}
	return out
}

func Filter(enabled []string) []Model {
	return filterRegistry(enabled, registry)
}

func (m Model) SupportsResolution(resolution string) bool {
	return contains(m.SupportedResolutions, resolution)
}

func (m Model) SupportsRatio(ratio string) bool {
	return contains(m.SupportedRatios, ratio)
}

func (m Model) SupportsDuration(duration int) bool {
	return duration >= m.DurationSeconds.Min && duration <= m.DurationSeconds.Max
}

func contains(items []string, needle string) bool {
	for _, item := range items {
		if item == needle {
			return true
		}
	}
	return false
}
