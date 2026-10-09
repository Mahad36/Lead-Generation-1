# Scraping (Google Maps leads)

Each user picks the lead source in **AI & settings → Google Maps leads** (`user_settings.scraper_provider`):

| Source | How | Needs |
| --- | --- | --- |
| **Built-in scraper** (default) | `app/services/scraper.py`, headless Chromium via Playwright on this PC | Nothing (Playwright Chromium is installed by the setup) |
| **Apify** | `app/services/apify_maps.py`, actor `compass~crawler-google-places` | Apify API token; saving "Apify" without one is refused |

`SCRAPER_PROVIDER` in `.env` only sets the default for users who never chose (`scraper`, `apify`, or `auto` = Apify when a
token exists).

## Built-in scraper

1. Opens `google.com/maps/search/<type> in <city/state>, <country>` (images and fonts blocked) and scrolls the results list.
2. Reads every result card in one pass (`parse_card`): name, category, address, phone, website, rating, opening hours,
   coordinates, booking link, Maps URL / place id. No place page is opened, which keeps it fast.
3. While the list keeps loading, each business website is checked in parallel (`website_contacts`,
   `SCRAPER_SITE_WORKERS`=10 at a time, `SCRAPER_SITE_PAGES`=3 pages, `SCRAPER_SITE_TIMEOUT_SECONDS`=8) with the app's
   crawler: emails (including Cloudflare-protected ones; placeholder addresses such as `example@mysite.com` are ignored),
   extra phones, social profiles. Browser user agent, one retry on connection/DNS errors.
4. Each business is saved as soon as its website check ends. Businesses without an email are skipped (counted as
   "skipped without email"); the rest go to the website check → automatic outreach pipeline.

Measured: 20 plumbers in Miami in ~15–25 s, emails found for ~2 of 3 businesses with a website.

Limits: Google Maps lists show the star rating but not the review count, so `review_count` stays empty. After very heavy
use Google shows a CAPTCHA / "unusual traffic" page: the scrape stops (`blocked`), keeps the leads found so far, and does
not try to get around it. Scraping Google Maps may be against Google's terms; use it at your own risk or switch to Apify.
