package pricing

import "math"

type Estimate struct {
	EstimatedTokens  int
	EstimatedCostUSD float64
	MaxCostUSD       float64
}

var tokensPerSecond = map[string]map[string]int{
	"dreamina-seedance-2-0": {
		"480p":  10128,
		"720p":  21780,
		"1080p": 49005,
	},
	"dreamina-seedance-2-0-fast": {
		"480p":  10128,
		"720p":  21780,
		"1080p": 49005,
	},
	"seedance-1-5-pro": {
		"480p":  7652,
		"720p":  16456,
		"1080p": 37027,
	},
}

var fallbackTokensPerSecond = map[string]int{
	"480p":  7652,
	"720p":  16456,
	"1080p": 37027,
}

var outputRate = map[string]float64{
	"seedance-1-0-lite-t2v-250428": 0.00153,
	"seedance-1-0-lite-i2v-250428": 0.00153,
	"seedance-1-0-pro-250528":      0.002375,
	"seedance-1-0-pro-fast-251015": 0.0009,
	"seedance-1-5-pro-251215":      0.00216,
}

var dreamina20Rate = map[bool]map[string]float64{
	false: {
		"480p":  0.007,
		"720p":  0.007,
		"1080p": 0.0077,
	},
	true: {
		"480p":  0.0043,
		"720p":  0.0043,
		"1080p": 0.0047,
	},
}

var dreamina20FastRate = map[bool]map[string]float64{
	false: {
		"480p":  0.0056,
		"720p":  0.0056,
		"1080p": 0.0056,
	},
	true: {
		"480p":  0.0033,
		"720p":  0.0033,
		"1080p": 0.0033,
	},
}

func EstimateVideo(model, resolution string, duration int, frames *int, hasVideoRef, generateAudio bool) Estimate {
	if resolution == "" {
		resolution = "720p"
	}
	seconds := float64(duration)
	if frames != nil {
		seconds = float64(*frames) / 24.0
	} else if duration <= 0 {
		if isV2(stripDate(model)) {
			seconds = 15
		} else {
			seconds = 12
		}
	}

	base := stripDate(model)
	perSecond := fallbackTokensPerSecond[resolution]
	if byResolution, ok := tokensPerSecond[base]; ok {
		if value, ok := byResolution[resolution]; ok {
			perSecond = value
		}
	}
	tokens := int(math.Ceil(float64(perSecond) * seconds))
	rate := outputRateFor(model, resolution, hasVideoRef)
	cost := round6(float64(tokens) / 1000 * rate)
	bufferPct := 0.10
	if generateAudio {
		bufferPct += 0.05
	}
	maxCost := round6(cost * (1 + bufferPct))
	return Estimate{
		EstimatedTokens:  tokens,
		EstimatedCostUSD: cost,
		MaxCostUSD:       maxCost,
	}
}

func ActualVideoCost(model, resolution string, completionTokens int64, hasVideoRef bool) float64 {
	return round6(float64(completionTokens) / 1000 * outputRateFor(model, resolution, hasVideoRef))
}

func outputRateFor(model, resolution string, hasVideoRef bool) float64 {
	if model == "dreamina-seedance-2-0-260128" {
		if byResolution, ok := dreamina20Rate[hasVideoRef]; ok {
			if rate, ok := byResolution[resolution]; ok {
				return rate
			}
		}
		return 0.0077
	}
	if model == "dreamina-seedance-2-0-fast-260128" {
		if byResolution, ok := dreamina20FastRate[hasVideoRef]; ok {
			if rate, ok := byResolution[resolution]; ok {
				return rate
			}
		}
		return 0.0056
	}
	return outputRate[model]
}

func stripDate(model string) string {
	if len(model) < 7 {
		return model
	}
	suffix := model[len(model)-6:]
	for _, ch := range suffix {
		if ch < '0' || ch > '9' {
			return model
		}
	}
	return model[:len(model)-7]
}

func isV2(base string) bool {
	return base == "seedance-1-5-pro" || base == "dreamina-seedance-2-0" || base == "dreamina-seedance-2-0-fast"
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}
