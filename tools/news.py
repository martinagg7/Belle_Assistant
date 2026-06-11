"""News headlines via RSS (no API limits)."""

import feedparser


# El País RSS feeds by category
FEEDS = {
    "general":       "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada",
    "deportes":      "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/deportes/portada",
    "economia":      "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/economia/portada",
    "cultura":       "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/cultura/portada",
    "internacional": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada",
}

SUMMARY_MAX = 80   # max summary length
NUM_NEWS    = 3    # number of headlines to return


def get_news(categoria: str = "general") -> str:
    url = FEEDS.get(categoria.lower(), FEEDS["general"])

    try:
        feed = feedparser.parse(url)

        if not feed.entries:
            return f"error: could not fetch news for {categoria}"

        items = []
        for i, entry in enumerate(feed.entries[:NUM_NEWS]):
            title   = entry.get("title", "sin título").strip()
            summary = entry.get("summary", "").strip()

            if len(summary) > SUMMARY_MAX:  # avoid cutting words
                summary = summary[:SUMMARY_MAX].rsplit(' ', 1)[0] + "..."

            items.append(f"{i+1}. {title} — {summary}")

        return f"noticias de {categoria} (El País):\n" + "\n".join(items)

    except Exception as e:
        return f"error: could not connect to the news feed ({e})"
