package main

import "testing"

func TestAdCarouselPushKeyIncludesInterval(t *testing.T) {
	items := []AdCarouselItem{
		{ID: "ad_1", Image: "https://example.com/a.webp", URL: "https://example.com"},
	}

	slowKey := adCarouselPushKey(items, 5200)
	fastKey := adCarouselPushKey(items, 3200)

	if slowKey == "" || fastKey == "" {
		t.Fatalf("adCarouselPushKey returned empty key")
	}
	if slowKey == fastKey {
		t.Fatalf("adCarouselPushKey should change when interval changes")
	}
}
