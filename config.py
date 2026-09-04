"""Configuration for the Liverpool Philharmonic Hall scraper."""

SITE_ID = "philharmonic_hall"
BASE_URL = "https://www.liverpoolphil.com/"
PAGES = [
    #("https://www.liverpoolphil.com/whats-on/?menu%5BeventCategories%5D=Film", "Drama"),
    ("https://www.liverpoolphil.com/whats-on/?menu%5BeventCategories%5D=Family","musical",),
    ("https://www.liverpoolphil.com/whats-on/?menu%5BeventCategories%5D=Film%20with%20Live%20Orchestra","musical",),
    ("https://www.liverpoolphil.com/whats-on/?menu%5BeventCategories%5D=Family%20event%20%28Non-RLPO%29","musical",),
]


# LISTING_URL = f"{BASE_URL}whats-on"
RUN_HEADLESS = True
DEFAULT_CURRENCY = "GBP"

# The seating-area dropdown includes a combined, zoomed-out overview area
# (no individually priced seats, just clickable zones for the real areas)
# alongside the actual bookable sub-areas. Belfast Grand Opera House's
# config has the same concept under a venue-specific label ("The Matcham
# Auditorium") — this is Liverpool Philharmonic Hall's equivalent.
COMBINED_SEATING_AREA_LABEL = "Main Auditorium"

DEFAULT_VENUE_DETAILS = {
    "venue": "Liverpool Philharmonic Hall",
    "address": "36 Hope Street, Liverpool, L1 9BP",
    "city": "Liverpool",
    "country": "UK",
}

VENUE_ADDRESS_MAP = {
    "liverpool philharmonic hall": DEFAULT_VENUE_DETAILS,
    "music room": {
        "venue": "Music Room",
        "address": "Hope Street, Liverpool, L1 9BP",
        "city": "Liverpool",
        "country": "UK",
    },
    "tung auditorium": {
        "venue": "Tung Auditorium",
        "address": "Yoko Ono Lennon Centre, University of Liverpool, Liverpool, L69 7ZZ",
        "city": "Liverpool",
        "country": "UK",
    },
}

SELECTORS = {
    "cookie_accept": "#ccc-notify-accept, #ccc-dismiss-button, .ccc-notify-button",
    "hits_item": "li.ais-Hits-item",
    "event_card": "div.c-media.c-media--event",
    "card_more_info_link": "a.o-button--secondary",
    "pagination_next": "a.c-pagination__next",
    "title": "h1.c-page-header__title",
    "pretitle": "p.c-page-header__pre-title",
    "book_now_button": "a.o-button--primary[href*='book']",
    "meta_start_date": "dl.c-page-header__meta time[itemprop='startDate']",
    "meta_end_date": "dl.c-page-header__meta time[itemprop='endDate']",
    "meta_dds": "dl.c-page-header__meta > dd",  # direct-child dd's only: [date, venue] or [date, venue, price] for single-performance shows (tags live in a nested wrapper div, excluded) — venue is always index 1
    "instances_list": "ul.o-list.o-block__content li.c-instance",
    "instance_date": "time.c-instance__heading-time",
    "instance_time": ".c-instance__meta-item:nth-child(1) dd time",
    "instance_venue": ".c-instance__meta-item:nth-child(2) dd",
    "instance_availability": ".c-instance__action .c-availability",
    "instance_booking_link": ".c-instance__action a.o-button",
    "seating_dropdown": "select[id*='AvailableAreas']",
    "all_seats": "img.Seat.NotDimmed, img.SeatSelectable.NotDimmed",
    "available_seats": "img.SeatSelectable.NotDimmed",
}
