"""Liverpool Philharmonic Hall extractor implementation using the framework."""
import json
import random
import re
import sys
import time
from datetime import timedelta
from urllib.parse import urljoin

import pandas as pd
from dateutil import parser
from selenium.webdriver.common.by import By
from seleniumbase import SB

from utils.base_extractor import BaseExtractor
from utils.logger import setup_logger
from utils.scraping_helpers import (
    convert_to_24hr,
    extract_postcode,
    format_datetime_key,
    get_currency_from_price,
    get_scrape_datetime,
    human_delay,
    human_scroll,
    normalize_country,
    standardize_category,
)

from .philharmonic_hall_config import (
    BASE_URL,
    COMBINED_SEATING_AREA_LABEL,
    DEFAULT_CURRENCY,
    DEFAULT_VENUE_DETAILS,
    PAGES,
    RUN_HEADLESS,
    SELECTORS,
    VENUE_ADDRESS_MAP,
)

logger = setup_logger(__name__, log_to_file=False)


class PhilharmonicHallExtractor(BaseExtractor):
    """Extractor for the Liverpool Philharmonic Hall website."""

    def __init__(self, local_test=False, show_count=2, **kwargs):
        """Set up the extractor's site_id/logging/local-test config via BaseExtractor."""
        super().__init__(
            site_id="philharmonic_hall",
            log_to_file=False,
            log_to_terminal=True,
            local_test=local_test,
            show_count=show_count,
            **kwargs,
        )
        self.all_data = []

    def safe_get(self, sb, url, wait=10):
        """Navigate to `url` via UC-reconnect, solving a captcha/bot-check if one appears.

        Returns True on success, None on failure (caller treats None as
        "page didn't load" and retries).
        """
        try:
            sb.uc_open_with_reconnect(url, reconnect_time=wait if wait > 4 else 4)
            # Bot-protection detection: the page loaded but landed on a
            # challenge screen instead of the real content.
            if (
                "captcha" in sb.get_current_url().lower()
                or "distil" in sb.get_page_source().lower()
            ):
                self.custom_logger.warning("Bot protection detected. Solving...")
                sb.uc_gui_handle_captcha()
                time.sleep(random.uniform(2, 4))
            self.custom_logger.info("Page loaded successfully: %s", url)
            return True
        except Exception as e:
            self.custom_logger.error(
                "Failed to load page: %s | Exception: %s", url, repr(e)
            )
            return None

    def accept_cookies(self, sb):
        """Dismiss the cookie-consent banner if it's currently visible; no-op otherwise."""
        cookie_selector = SELECTORS["cookie_accept"]
        try:
            if sb.is_element_visible(cookie_selector):
                human_delay(1, 2.5)
                sb.click(cookie_selector)
                human_delay(2, 3)
        except Exception:
            pass

    def clean_title(self, title: str):
        """Remove \n from title."""
        return title.replace("\n", " ").strip() if title else None

    def get_show_links(self, sb) -> list[str]:
        """Collect '/events/{slug}' detail links from the listing grid.

        The results grid mixes real event cards (div.c-media--event) with
        promotional tiles (a.c-block-link, e.g. "What's on for Families") —
        only cards containing an event_card element are kept.
        """
        links = []

        # The results grid is client-side rendered (Algolia InstantSearch),
        # not present in the initial HTML -- wait for it deterministically
        # instead of relying on the fixed human_delay() before this call
        # being long enough. A cold browser session's very first page load
        # (JS bundle fetch/init, no cache) is the slowest one, so whichever
        # category happens to be first in PAGES is the most exposed to this
        # race. A timeout here isn't fatal -- 0 genuine results is valid too.
        try:
            sb.wait_for_element_present(SELECTORS["hits_item"], timeout=15)
        except Exception:
            pass

        cards = sb.find_elements(By.CSS_SELECTOR, SELECTORS["hits_item"])
        for card in cards:
            try:
                # Skip promo tiles: they don't contain an event_card element.
                card.find_element(By.CSS_SELECTOR, SELECTORS["event_card"])
            except Exception:
                continue
            try:
                # "More info" link -> the show's own detail page.
                href = card.find_element(
                    By.CSS_SELECTOR, SELECTORS["card_more_info_link"]
                ).get_attribute("href")
                if href:
                    # href can be relative ("/events/...") or absolute -- normalize either way.
                    links.append(urljoin(BASE_URL, href))
            except Exception:
                continue
        return links

    def get_next_page_url(self, sb) -> str | None:
        """Return the "Next" pagination link's href, or None once it's disabled (last page)."""
        try:
            next_link = sb.find_element(SELECTORS["pagination_next"])
            classes = next_link.get_attribute("class") or ""
            if "is-disabled" in classes:
                return None
            return next_link.get_attribute("href")
        except Exception:
            return None

    def _get_show_title(self, sb) -> str | None:
        """Read and clean the show detail page's <h1> title."""
        try:
            title = sb.get_text(SELECTORS["title"]).strip()
            return self.clean_title(title) or None
        except Exception:
            return None

    def _get_pretitle(self, sb) -> str | None:
        """Read the small pretitle line above the show title (e.g. an orchestra/season label), if present."""
        try:
            return sb.get_text(SELECTORS["pretitle"]).strip() or None
        except Exception:
            return None

    def _get_header_dates(self, sb) -> tuple[str | None, str | None]:
        """Read open/close dates directly from the <time itemprop> ISO datetime attrs."""
        # startDate is always present; endDate only exists for shows that
        # run across more than one date.
        try:
            start_iso = sb.find_element(SELECTORS["meta_start_date"]).get_attribute(
                "datetime"
            )
        except Exception:
            start_iso = None
        try:
            end_iso = sb.find_element(SELECTORS["meta_end_date"]).get_attribute(
                "datetime"
            )
        except Exception:
            end_iso = None

        open_date = parser.parse(start_iso).strftime("%Y-%m-%d") if start_iso else None
        # Single-date show: no endDate element, so close_date == open_date.
        close_date = (
            parser.parse(end_iso).strftime("%Y-%m-%d") if end_iso else open_date
        )
        return open_date, close_date

    def _get_venue_details(self, sb) -> dict:
        """Read the venue name from the page header, resolve to a known address.

        The header's direct-child <dd> elements are always [date, venue],
        with an optional third "price range" <dd> appended for single-
        performance shows — venue is always index 1, never the last one.
        """
        try:
            dds = sb.find_elements(By.CSS_SELECTOR, SELECTORS["meta_dds"])
            venue_name = dds[1].text.strip() if len(dds) > 1 else None
        except Exception:
            venue_name = None

        # No venue name scraped at all -- fall back to the default venue.
        if not venue_name:
            return DEFAULT_VENUE_DETAILS

        # Known venue (Liverpool Philharmonic Hall / Music Room / Tung
        # Auditorium) -- use its mapped address/city/country.
        details = VENUE_ADDRESS_MAP.get(venue_name.lower())
        if details:
            return details

        # Unrecognized venue name -- keep it, but fall back to the default
        # address/city/country rather than leaving them blank.
        return {**DEFAULT_VENUE_DETAILS, "venue": venue_name}

    def _extract_performances(self, sb) -> list[dict]:
        """Return every performance for this show.

        Multi-performance shows list each instance under '#dates-and-times'
        — already present in the DOM on page load, no click needed (the
        "Book now" link to that anchor is a same-page scroll). Single-
        performance shows have no such list at all: the header's own
        <time itemprop="startDate"> datetime attribute carries the one
        performance's date and time together, and "Book now" instead links
        directly to that performance's own booking page.
        """
        rows = sb.find_elements(By.CSS_SELECTOR, SELECTORS["instances_list"])
        self.custom_logger.info("Found %d performance instance(s)", len(rows))

        if rows:
            self.custom_logger.info(
                "Parsing %d multiple performance instance(s)...", len(rows)
            )
            return self._extract_performances_from_instances(rows)

        self.custom_logger.info(
            "No performance instance list found — parsing single performance..."
        )
        return self._extract_single_performance(sb)

    def _extract_performances_from_instances(self, rows) -> list[dict]:
        """Parse each '#dates-and-times' <li class="c-instance"> row into a performance dict."""
        performances = []
        for row in rows:
            try:
                date_iso = row.find_element(
                    By.CSS_SELECTOR, SELECTORS["instance_date"]
                ).get_attribute("datetime")
                time_iso = row.find_element(
                    By.CSS_SELECTOR, SELECTORS["instance_time"]
                ).get_attribute("datetime")
                if not date_iso or not time_iso:
                    continue

                try:
                    availability = (
                        row.find_element(
                            By.CSS_SELECTOR, SELECTORS["instance_availability"]
                        )
                        .text.strip()
                        .lower()
                    )
                except Exception:
                    availability = ""

                try:
                    booking_url = (
                        row.find_element(
                            By.CSS_SELECTOR, SELECTORS["instance_booking_link"]
                        ).get_attribute("href")
                        or ""
                    )
                except Exception:
                    booking_url = ""

                # Sold-out/cancelled performances, and phone/email-only
                # bookings (mailto: links), have no real seat map to scrape --
                # clear the booking_url so extract_seat_metrics skips them
                # instead of trying to navigate to a non-booking link.
                is_unavailable = any(
                    term in availability for term in ("sold out", "cancelled")
                )
                if booking_url.startswith("mailto:") or is_unavailable:
                    booking_url = ""

                performances.append(
                    {"date": date_iso, "time": time_iso, "booking_url": booking_url}
                )
            except Exception as inner_e:
                self.custom_logger.debug("Instance row parsing failed: %s", inner_e)
                continue

        return performances

    def _extract_single_performance(self, sb) -> list[dict]:
        """No '#dates-and-times' list exists for this show — derive the one
        performance from the header date and the 'Book now' link, which
        points directly at that performance's own booking page.
        """
        try:
            start_iso = sb.find_element(SELECTORS["meta_start_date"]).get_attribute(
                "datetime"
            )
        except Exception:
            start_iso = None
        if not start_iso:
            return []

        try:
            dt = parser.parse(start_iso)
        except Exception:
            return []

        try:
            booking_url = (
                sb.find_element(SELECTORS["book_now_button"]).get_attribute("href")
                or ""
            )
        except Exception:
            booking_url = ""
        if "#dates-and-times" in booking_url:
            # A stale/unexpected anchor link with no real single-instance
            # booking page behind it -- treat as unbookable rather than
            # trying to scrape a seat map from a non-booking page.
            booking_url = ""

        return [
            {
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "booking_url": booking_url,
            }
        ]

    def extract_seats(self, sb) -> tuple[list, int | None, str | None]:
        """Extract seats and pricing from the currently-loaded Spektrix seat plan.

        The seating-area <select> may render directly on the page or inside an
        iframe depending on the venue (mirrors belfast_grand_opera_house, which
        scrapes the same ASP.NET Spektrix seat-plan widget).
        """
        seat_data = []
        perf_capacity = 0
        currency = None

        try:
            sb.wait_for_ready_state_complete()
            human_delay(2, 3)

            dropdown_selector = SELECTORS["seating_dropdown"]
            has_dropdown = False

            # Some venues (e.g. Music Room) render the seat plan directly on
            # the page with no area dropdown at all -- check for it first.
            try:
                sb.wait_for_element_present(dropdown_selector, timeout=15)
                has_dropdown = True
            except Exception:
                pass

            # Not found on the main page -- it may be inside an iframe instead.
            if not has_dropdown:
                try:
                    iframes = sb.find_elements("iframe")
                    for iframe in iframes:
                        try:
                            sb.switch_to_frame(iframe)
                            human_delay(2, 3)
                            sb.wait_for_element_present(dropdown_selector, timeout=15)
                            has_dropdown = True
                            break
                        except Exception:
                            sb.switch_to_default_content()
                except Exception as iframe_err:
                    self.custom_logger.warning("iframe search failed: %s", iframe_err)

            if has_dropdown:
                # Read every <option> label from the area dropdown via JS
                # (faster and more reliable than iterating Selenium elements).
                raw_options = sb.execute_script(
                    """
                    var select = document.querySelector(arguments[0]);
                    if (!select) return [];
                    var options = [];
                    for (var i = 0; i < select.options.length; i++) {
                        options.push(select.options[i].text.trim());
                    }
                    return options;
                    """,
                    dropdown_selector,
                )
                # Drop the combined overview option (e.g. "Main Auditorium")
                # -- it's a zoomed-out clickable map of the real sub-areas
                # below, not individually priced seats of its own.
                areas = [
                    o for o in raw_options if o and o != COMBINED_SEATING_AREA_LABEL
                ]
                self.custom_logger.info("Found seating areas: %s", areas)
            else:
                # No dropdown anywhere -- treat the whole plan as one area.
                self.custom_logger.info("No area dropdown — using single-level seating")
                areas = ["Seating"]

            prev_seat_count = -1

            for area in areas:
                try:
                    if has_dropdown:
                        # Select this area by matching its visible label text,
                        # then fire a native 'change' event so the ASP.NET
                        # postback that re-renders the seat map actually fires.
                        selected = sb.execute_script(
                            """
                            var select = document.querySelector(arguments[0]);
                            if (!select) return false;
                            var areaName = arguments[1];
                            for (var i = 0; i < select.options.length; i++) {
                                if (select.options[i].text.trim() === areaName) {
                                    select.value = select.options[i].value;
                                    select.dispatchEvent(new Event('change', { bubbles: true }));
                                    return true;
                                }
                            }
                            return false;
                            """,
                            dropdown_selector,
                            area,
                        )
                        if not selected:
                            self.custom_logger.warning(
                                "Could not select area: %s", area
                            )
                            continue

                        # Wait for the postback to finish re-rendering: poll
                        # until the seat count changes from the previous
                        # area's count, proving this area's own seats loaded
                        # (a stale/unchanged count means we're still looking
                        # at the previous area's chart mid-render).
                        sb.wait_for_ready_state_complete()
                        for _ in range(15):
                            human_delay(2, 3)
                            _cur_count = len(
                                sb.find_elements(
                                    By.CSS_SELECTOR, SELECTORS["all_seats"]
                                )
                            )
                            if _cur_count > 0 and _cur_count != prev_seat_count:
                                break

                    # Total seats (available + unavailable) in this area, for capacity.
                    all_seats = sb.find_elements(
                        By.CSS_SELECTOR, SELECTORS["all_seats"]
                    )
                    area_capacity = len(all_seats)
                    prev_seat_count = area_capacity
                    perf_capacity += area_capacity
                    self.custom_logger.info("Area: %s | Seats: %s", area, area_capacity)

                    # Pull every available seat's tooltip/title text in one JS
                    # call rather than round-tripping per element via Selenium.
                    seat_tooltips = sb.execute_script(
                        """
                        var elems = document.querySelectorAll(arguments[0]);
                        var out = [];
                        for (var i = 0; i < elems.length; i++) {
                            out.push(elems[i].getAttribute('tooltip') || elems[i].getAttribute('title') || '');
                        }
                        return out;
                        """,
                        SELECTORS["available_seats"],
                    )

                    for tooltip in seat_tooltips:
                        if not tooltip or tooltip.lower() == "unavailable":
                            continue
                        # Tooltip format: "A8 - £14.30" or "A8 - £14.30 - This
                        # is an aisle seat" -- only the seat id + price matter.
                        match = re.match(r"^([A-Za-z0-9]+)\s*-\s*£?([\d,.]+)", tooltip)
                        if not match:
                            continue
                        seat_id = match.group(1)
                        ticket_price = float(match.group(2).replace(",", ""))
                        seat_data.append(
                            {
                                # Prefix with the area name so seat ids don't
                                # collide across areas (e.g. both "Stalls" and
                                # "Circles" can have an "A1").
                                "seat": f"{area} {seat_id}",
                                "ticket_price": ticket_price,
                            }
                        )
                        if currency is None:
                            currency = get_currency_from_price(tooltip)

                except Exception as area_error:
                    self.custom_logger.warning(
                        "Failed to process area %s: %s", area, area_error
                    )
                    continue

        except Exception as e:
            self.custom_logger.error("Seat map scraping failed: %s", e)
        finally:
            try:
                sb.switch_to_default_content()
            except Exception:
                pass

        return seat_data, (perf_capacity if perf_capacity > 0 else None), currency

    def extract_seat_metrics(
        self, sb, performances: list
    ) -> tuple[dict, str | None, int | None]:
        """Visit each performance's booking page and collect its seat pricing.

        Returns (seat_pricing dict keyed by "YYYY-MM-DD HH:MM", currency,
        overall capacity — the max seat count seen across performances).
        """
        seat_pricing = {}
        capacity_values = []
        currency = None
        encountered_no_seatmap = False

        for i, perf in enumerate(performances, start=1):
            key = format_datetime_key(perf["date"], perf["time"])
            if not key:
                continue

            self.custom_logger.info(
                "[%d/%d] Seats for %s %s",
                i,
                len(performances),
                perf["date"],
                perf["time"],
            )

            # No bookable link at all (sold out / cancelled / phone-only) --
            # record an empty seat list for this performance and move on.
            if not perf["booking_url"]:
                self.custom_logger.info(
                    "Performance %s has no bookable link (sold out/cancelled/phone-only).",
                    key,
                )
                seat_pricing[key] = []
                continue

            if not self.safe_get(sb, perf["booking_url"]):
                seat_pricing[key] = []
                encountered_no_seatmap = True
                continue

            human_delay(3, 5)
            seats, capacity, curr = self.extract_seats(sb)

            if seats:
                seat_pricing[key] = seats
                if capacity:
                    capacity_values.append(capacity)
                if curr and currency is None:
                    currency = curr
            else:
                seat_pricing[key] = []
                encountered_no_seatmap = True

            human_delay(5, 7)

        # Distinguish "every performance is genuinely sold out" (each key
        # legitimately maps to an empty list) from "the seat map layout
        # itself never loaded for any performance" (a scraping failure) --
        # in the latter case, blank out seat_pricing entirely rather than
        # reporting a false "sold out" for the whole show.
        if encountered_no_seatmap and all(len(v) == 0 for v in seat_pricing.values()):
            self.custom_logger.info(
                "All performances lack a seat map layout. Resetting seat_pricing = {}"
            )
            seat_pricing = {}

        capacity = max(capacity_values) if capacity_values else None
        return seat_pricing, currency, capacity

    def _scrape_one_show(self, sb, show_url: str, category: str) -> dict | None:
        """Scrape a single show page end-to-end.

        Returns a completed row dict on success, or None if the show page
        did not render, has no future performances, or hit an unexpected
        error — the caller retries. The whole body is wrapped in one
        try/except so a single bad show (e.g. a malformed date somewhere in
        its performances) can't raise past this function and kill the rest
        of the category's shows, or every category after it -- _scrape_shows
        and extract()'s category loop are never wrapped themselves and rely
        on this boundary to isolate failures.
        """
        try:
            if not self.safe_get(sb, show_url):
                return None

            title = self._get_show_title(sb)
            if not title:
                self.custom_logger.warning("No title found for: %s", show_url)
                return None

            venue_url = sb.get_current_url()

            self.accept_cookies(sb)
            human_delay(2, 4)

            open_date, close_date = self._get_header_dates(sb)
            venue_details = self._get_venue_details(sb)

            venue_name = venue_details.get("venue", DEFAULT_VENUE_DETAILS["venue"])
            address = venue_details.get("address", DEFAULT_VENUE_DETAILS["address"])
            city = venue_details.get("city", DEFAULT_VENUE_DETAILS["city"])
            country = normalize_country(venue_details.get("country", DEFAULT_VENUE_DETAILS["country"]))

            
            self.custom_logger.info("Title: %s", title)
            self.custom_logger.info("Category: %s", category)
            self.custom_logger.info("Header dates: %s - %s", open_date, close_date)
            self.custom_logger.info("Venue: %s", venue_name)

            human_delay(3, 5)
            human_scroll(sb)
            time.sleep(3)

            performances = self._extract_performances(sb)
            if not performances:
                self.custom_logger.warning(
                    "No performances found for '%s', skipping", title
                )
                return None

            # Fall back to computing open/close dates from the actual
            # performance list when the header didn't supply them (or
            # supplied something inconsistent with the real performances).
            sorted_dates = sorted(p["date"] for p in performances)
            if not open_date:
                open_date = sorted_dates[0]
            if not close_date:
                close_date = sorted_dates[-1]
            if open_date > close_date:
                open_date = sorted_dates[0]

            seat_pricing, currency, capacity = self.extract_seat_metrics(
                sb, performances
            )

            self.custom_logger.info(
                "Performances: %d | Seat keys: %d",
                len(performances),
                len(seat_pricing),
            )
            self.custom_logger.info("Capacity: %s | Currency: %s", capacity, currency)

            return {
                "title": title,
                "category": standardize_category(category),
                "venue": venue_name,
                "venue_url": venue_url,
                "address": address,
                "city": city,
                "country": country,
                "open_date": open_date,
                "close_date": close_date,
                "booking_start_date": open_date,
                "booking_end_date": close_date,
                "upcoming_performances": [
                    {"date": p["date"], "time": p["time"]} for p in performances
                ],
                "seat_pricing": seat_pricing,
                "capacity": int(capacity) if capacity is not None else None,
                "currency": currency or DEFAULT_CURRENCY,
                "is_limited_run": None,
                "scrape_datetime": get_scrape_datetime(),
            }
        except Exception as e:
            # Catch-all safety net: log which show and why, then let the
            # caller's retry logic handle it like any other failed show,
            # instead of this exception propagating up and aborting every
            # remaining show in every remaining category.
            self.custom_logger.error(
                "Unexpected error scraping '%s': %s", show_url, repr(e)
            )
            return None

    def _scrape_shows(self, sb, show_links: list, category: str) -> None:
        """Scrape individual show pages with multi-pass retry (Leicester Curve pattern)."""
        _MAX_PASSES = 3
        pending = list(show_links)
        total_shows = len(show_links)
        # Fixed show number per URL, based on its original position in
        # show_links -- not on which retry pass actually processes it, so
        # "[Family] Show 3/8" always refers to the same show even if it gets
        # deferred to a later pass. This is what to watch in the logs to see
        # how far a run has gotten.
        show_numbers = {url: i + 1 for i, url in enumerate(show_links)}

        # Retry loop: a show that fails to render (bot challenge, timeout)
        # gets deferred to the next pass instead of being dropped outright,
        # up to _MAX_PASSES attempts.
        for _pass in range(1, _MAX_PASSES + 1):
            if not pending:
                break

            self.custom_logger.info(
                "Show pass %d/%d — %d show(s)", _pass, _MAX_PASSES, len(pending)
            )
            still_pending = []

            for show_url in pending:
                self.custom_logger.info(
                    "[%s] Show %d/%d: %s",
                    category,
                    show_numbers[show_url],
                    total_shows,
                    show_url,
                )
                row = self._scrape_one_show(sb, show_url, category)
                if row is None:
                    still_pending.append(show_url)
                    self.custom_logger.warning(
                        "Pass %d: show deferred — %s", _pass, show_url
                    )
                else:
                    self.all_data.append(row)
                    self.log_record(row)
                    human_delay(8, 15)

            pending = still_pending

            # Cool down before the next pass so a burst of failures (likely
            # bot-detection related) doesn't just retry immediately.
            if pending and _pass < _MAX_PASSES:
                self.custom_logger.info(
                    "Pass %d complete — %d show(s) still pending. "
                    "Cooling down before pass %d",
                    _pass,
                    len(pending),
                    _pass + 1,
                )
                human_scroll(sb)
                human_delay(60, 120)

        if pending:
            self.custom_logger.warning(
                "%d show(s) could not be scraped after %d passes: %s",
                len(pending),
                _MAX_PASSES,
                pending,
            )

    def extract(self) -> bytes:
        """Open SB session, scrape all shows across PAGES, return JSON bytes."""
        self.all_data = []
        seen_links: set[str] = set()

        # uc=True: undetected-Chrome mode, needed to get past any bot
        # protection on the listing/detail/booking pages.
        with SB(
            uc=True,
            test=True,
            headless=RUN_HEADLESS,
            browser="chrome",
            locale="en-US",
            chromium_arg="--enable-features=TranslateUI",
        ) as sb:
            self.custom_logger.info(
                "Starting extraction from Liverpool Philharmonic Hall"
            )

            for url, category in PAGES:
                self.custom_logger.info("[Listing] %s: %s", category, url)
                if not self.safe_get(sb, url):
                    continue

                human_delay(4, 6)
                sb.maximize_window()
                self.accept_cookies(sb)

                # Page through this category's results until "Next" is disabled,
                # collecting every show link (deduped against every other category
                # via the shared seen_links set) before scraping any of them.
                show_links: list[str] = []
                page_url = url
                while page_url:
                    if page_url != url and not self.safe_get(sb, page_url):
                        break

                    human_delay(3, 5)
                    for link in self.get_show_links(sb):
                        if link not in seen_links:
                            seen_links.add(link)
                            show_links.append(link)

                    page_url = self.get_next_page_url(sb)
                    if page_url:
                        human_delay(2, 4)

                # In local/dev test runs, cap how many shows from this
                # category actually get scraped for a quick smoke test.
                if self.local_test:
                    self.custom_logger.info(
                        "LOCAL TEST MODE: Limiting to %s shows", self.show_count
                    )
                    show_links = show_links[: self.show_count]

                self._scrape_shows(sb, show_links, category)

        return json.dumps(self.all_data, default=str).encode("utf-8")

    def _parse(self, _raw: bytes):
        """Build DataFrame from self.all_data collected during extract()."""
        df = pd.DataFrame(self.all_data)
        self.custom_logger.info("Parsing completed. Extracted %s shows", len(df))
        return df


def main():
    """Example usage of the Liverpool Philharmonic Hall extractor."""
    extractor = PhilharmonicHallExtractor(
        save_csv_locally=False, csv_incremental_mode=False
    )
    result = extractor.run()
    logger.info(f"Extraction result: {result}")
    if result.get("status") != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
