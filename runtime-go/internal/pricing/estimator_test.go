package pricing

import "testing"

func TestSeedance2CurrentBytePlusRates(t *testing.T) {
	if got := ActualVideoCost("dreamina-seedance-2-0-260128", "480p", 1000, false); got != 0.007 {
		t.Fatalf("Seedance 2.0 480p no-video rate cost = %v", got)
	}
	if got := ActualVideoCost("dreamina-seedance-2-0-260128", "480p", 1000, true); got != 0.0043 {
		t.Fatalf("Seedance 2.0 480p video-ref rate cost = %v", got)
	}
	if got := ActualVideoCost("dreamina-seedance-2-0-260128", "1080p", 1000, false); got != 0.0077 {
		t.Fatalf("Seedance 2.0 1080p no-video rate cost = %v", got)
	}
	if got := ActualVideoCost("dreamina-seedance-2-0-fast-260128", "480p", 1000, false); got != 0.0056 {
		t.Fatalf("Seedance 2.0 Fast no-video rate cost = %v", got)
	}
}
