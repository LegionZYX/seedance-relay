package models

import "testing"

func TestFilterDefaultsToNativeModelIDsWhenAliasesAreConfigured(t *testing.T) {
	t.Setenv("MODEL_ID_ALIASES_JSON", `{"video-pro":"dreamina-seedance-2-0-260128"}`)
	aliasedRegistry := withAliases(nativeRegistry)

	defaultModels := filterRegistry(nil, aliasedRegistry)
	for _, model := range defaultModels {
		if model.ID == "video-pro" {
			t.Fatalf("default model list should not include configured alias: %#v", defaultModels)
		}
	}
	if len(defaultModels) != len(nativeRegistry) {
		t.Fatalf("default model list should only include native ids, got %d want %d", len(defaultModels), len(nativeRegistry))
	}

	explicitModels := filterRegistry([]string{"video-pro"}, aliasedRegistry)
	if len(explicitModels) != 1 || explicitModels[0].ID != "video-pro" {
		t.Fatalf("explicit alias should be available when selected: %#v", explicitModels)
	}
	if explicitModels[0].UpstreamID != "dreamina-seedance-2-0-260128" {
		t.Fatalf("alias should still forward native BytePlus model id, got %q", explicitModels[0].UpstreamID)
	}
}
