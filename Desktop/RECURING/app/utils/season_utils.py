"""
Geographic region and season detection system.
Handles Northern/Southern hemisphere season differences.
"""

from datetime import date, datetime
from enum import Enum
from typing import Optional


class Hemisphere(str, Enum):
    NORTHERN = "northern"
    SOUTHERN = "southern"
    EQUATORIAL = "equatorial"


class Season(str, Enum):
    SPRING = "spring"
    SUMMER = "summer"
    FALL = "fall"
    WINTER = "winter"


# Country to hemisphere mapping
HEMISPHERE_MAP = {
    # Northern Hemisphere
    "US": Hemisphere.NORTHERN,
    "USA": Hemisphere.NORTHERN,
    "CA": Hemisphere.NORTHERN,
    "CANADA": Hemisphere.NORTHERN,
    "UK": Hemisphere.NORTHERN,
    "GB": Hemisphere.NORTHERN,
    "DE": Hemisphere.NORTHERN,
    "GERMANY": Hemisphere.NORTHERN,
    "FR": Hemisphere.NORTHERN,
    "FRANCE": Hemisphere.NORTHERN,
    "IT": Hemisphere.NORTHERN,
    "ITALY": Hemisphere.NORTHERN,
    "ES": Hemisphere.NORTHERN,
    "SPAIN": Hemisphere.NORTHERN,
    "IN": Hemisphere.NORTHERN,
    "INDIA": Hemisphere.NORTHERN,
    "JP": Hemisphere.NORTHERN,
    "JAPAN": Hemisphere.NORTHERN,
    "CN": Hemisphere.NORTHERN,
    "CHINA": Hemisphere.NORTHERN,
    "RU": Hemisphere.NORTHERN,
    "RUSSIA": Hemisphere.NORTHERN,
    # Southern Hemisphere
    "AU": Hemisphere.SOUTHERN,
    "AUSTRALIA": Hemisphere.SOUTHERN,
    "NZ": Hemisphere.SOUTHERN,
    "NEW ZEALAND": Hemisphere.SOUTHERN,
    "AR": Hemisphere.SOUTHERN,
    "ARGENTINA": Hemisphere.SOUTHERN,
    "BR": Hemisphere.SOUTHERN,
    "BRAZIL": Hemisphere.SOUTHERN,
    "ZA": Hemisphere.SOUTHERN,
    "SOUTH AFRICA": Hemisphere.SOUTHERN,
    "CL": Hemisphere.SOUTHERN,
    "CHILE": Hemisphere.SOUTHERN,
    # Equatorial (minimal seasonal variation)
    "SG": Hemisphere.EQUATORIAL,
    "SINGAPORE": Hemisphere.EQUATORIAL,
    "MY": Hemisphere.EQUATORIAL,
    "MALAYSIA": Hemisphere.EQUATORIAL,
    "TH": Hemisphere.EQUATORIAL,
    "THAILAND": Hemisphere.EQUATORIAL,
    "ID": Hemisphere.EQUATORIAL,
    "INDONESIA": Hemisphere.EQUATORIAL,
    "KE": Hemisphere.EQUATORIAL,
    "KENYA": Hemisphere.EQUATORIAL,
    "NG": Hemisphere.EQUATORIAL,
    "NIGERIA": Hemisphere.EQUATORIAL,
}

# UTC offsets for major timezones (in hours from UTC)
TIMEZONE_OFFSETS = {
    "Pacific/Auckland": 13,
    "Australia/Sydney": 11,
    "Asia/Tokyo": 9,
    "Asia/Kolkata": 5.5,
    "Europe/London": 1,
    "Europe/Paris": 2,
    "America/New_York": -4,
    "America/Chicago": -5,
    "America/Denver": -6,
    "America/Los_Angeles": -7,
    "America/Sao_Paulo": -3,
}


def get_hemisphere(country: Optional[str]) -> Hemisphere:
    """
    Determine hemisphere from country code or name.
    Defaults to Northern if unknown.
    """
    if not country:
        return Hemisphere.NORTHERN

    country_upper = country.strip().upper()
    return HEMISPHERE_MAP.get(country_upper, Hemisphere.NORTHERN)


def get_current_season(
    reference_date: Optional[date] = None,
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> Season:
    """
    Get current season for a given hemisphere.

    Northern Hemisphere:
    - Spring: Mar, Apr, May
    - Summer: Jun, Jul, Aug
    - Fall: Sep, Oct, Nov
    - Winter: Dec, Jan, Feb

    Southern Hemisphere (opposite):
    - Spring: Sep, Oct, Nov
    - Summer: Dec, Jan, Feb
    - Fall: Mar, Apr, May
    - Winter: Jun, Jul, Aug

    Equatorial:
    - Always returns "summer" (warm) or could implement wet/dry seasons
    """
    if reference_date is None:
        reference_date = date.today()

    month = reference_date.month

    if hemisphere == Hemisphere.EQUATORIAL:
        return Season.SUMMER

    if hemisphere == Hemisphere.NORTHERN:
        if month in (3, 4, 5):
            return Season.SPRING
        elif month in (6, 7, 8):
            return Season.SUMMER
        elif month in (9, 10, 11):
            return Season.FALL
        else:  # 12, 1, 2
            return Season.WINTER

    else:  # Southern Hemisphere
        if month in (9, 10, 11):
            return Season.SPRING
        elif month in (12, 1, 2):
            return Season.SUMMER
        elif month in (3, 4, 5):
            return Season.FALL
        else:  # 6, 7, 8
            return Season.WINTER


def get_next_season(
    current_season: Season,
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> Season:
    """Get the next season coming up."""
    season_order = [Season.SPRING, Season.SUMMER, Season.FALL, Season.WINTER]
    current_index = season_order.index(current_season)
    next_index = (current_index + 1) % 4
    return season_order[next_index]


def get_season_dates(
    year: int,
    season: Season,
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> tuple[date, date]:
    """
    Get start and end dates for a specific season in a year.
    Returns (start_date, end_date).
    """
    if hemisphere == Hemisphere.EQUATORIAL:
        return (date(year, 1, 1), date(year, 12, 31))

    if hemisphere == Hemisphere.NORTHERN:
        season_months = {
            Season.SPRING: (3, 5),
            Season.SUMMER: (6, 8),
            Season.FALL: (9, 11),
            Season.WINTER: (12, 2),
        }
    else:  # Southern
        season_months = {
            Season.SPRING: (9, 11),
            Season.SUMMER: (12, 2),
            Season.FALL: (3, 5),
            Season.WINTER: (6, 8),
        }

    start_month, end_month = season_months[season]

    if season == Season.WINTER:
        if hemisphere == Hemisphere.NORTHERN:
            return (date(year, 12, 1), date(year + 1, 2, 28))
        else:
            return (date(year, 6, 1), date(year, 8, 31))
    elif season == Season.SUMMER and hemisphere == Hemisphere.SOUTHERN:
        return (date(year, 12, 1), date(year + 1, 2, 28))
    else:
        from calendar import monthrange

        end_day = monthrange(year, end_month)[1]
        return (date(year, start_month, 1), date(year, end_month, end_day))


def get_quarterly_campaign_week(
    year: int,
    season: Season,
    hemisphere: Hemisphere = Hemisphere.NORTHERN,
) -> tuple[date, date]:
    """
    Get the first week of the season for campaign launch.
    Returns (monday, sunday) of the first week.
    """
    season_start, _ = get_season_dates(year, season, hemisphere)

    days_since_monday = season_start.weekday()
    monday = season_start

    if days_since_monday > 0:
        from datetime import timedelta

        monday = season_start - timedelta(days=days_since_monday)

    from datetime import timedelta

    sunday = monday + timedelta(days=6)

    return (monday, sunday)


def get_customer_local_hour(
    customer_timezone: Optional[str],
    utc_time: Optional[datetime] = None,
) -> int:
    """
    Get the current hour in customer's local timezone.
    Returns hour (0-23) in customer's local time.
    """
    if utc_time is None:
        utc_time = datetime.utcnow()

    if not customer_timezone:
        return utc_time.hour

    offset = TIMEZONE_OFFSETS.get(customer_timezone, 0)
    local_hour = (utc_time.hour + int(offset)) % 24

    return local_hour


def is_optimal_send_time(
    customer_timezone: Optional[str],
    optimal_hour: int = 10,
    hour_window: int = 2,
) -> bool:
    """
    Check if current time is within optimal send window.
    Default: 10am +/- 2 hours (8am-12pm local time).
    """
    local_hour = get_customer_local_hour(customer_timezone)
    return abs(local_hour - optimal_hour) <= hour_window


def season_to_display_name(season: Season) -> str:
    """Convert season enum to display-friendly name."""
    names = {
        Season.SPRING: "spring",
        Season.SUMMER: "summer",
        Season.FALL: "fall",
        Season.WINTER: "winter",
    }
    return names[season]


def get_seasonal_style_keywords(season: Season) -> list[str]:
    """Get style keywords associated with each season."""
    keywords = {
        Season.SPRING: [
            "light layers",
            "breezy",
            "floral",
            "pastel",
            "fresh",
            "renewal",
            "lightweight",
            "transitional",
            "bloom",
        ],
        Season.SUMMER: [
            "light",
            "breathable",
            "linen",
            "cotton",
            "vacation",
            "sunny",
            "beach",
            "relaxed",
            "airy",
            "casual",
        ],
        Season.FALL: [
            "layered",
            "cozy",
            "earth tones",
            "warm",
            "textures",
            "knitwear",
            "jackets",
            "boots",
            "harvest",
            "crisp",
        ],
        Season.WINTER: [
            "warm",
            "insulated",
            "heavy",
            "wool",
            "cozy",
            "holiday",
            "festive",
            "indoors",
            "sweaters",
            "layering",
        ],
    }
    return keywords[season]
