"""The built-in library of proposals: value lists (XREF) and field-name based checks.

Everything here is a starting point written in advance. Nothing in this module changes a
project; the engine only offers these as proposals the user may accept."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from data_migration_concept.proposals.countries import ALPHA2, resolve_country

_CURRENCY_CODES = """
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD
GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR
NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP
STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF
XPF YER ZAR ZMW ZWL
"""
CURRENCIES = frozenset(_CURRENCY_CODES.split())  # ISO 4217
# Unit of measure: legacy spellings -> a common ISO / UN unit code. A starting point only: the
# target system's own unit codes decide the final values.
UOM = {
    "EA": "PC", "EACH": "PC", "PCS": "PC", "PC": "PC", "ST": "PC", "STK": "PC", "STUECK": "PC",
    "UNIT": "PC", "KG": "KG", "KGS": "KG", "KILO": "KG", "KILOGRAM": "KG", "G": "G", "GR": "G",
    "GRAM": "G", "LB": "LB", "LBS": "LB", "POUND": "LB", "L": "L", "LT": "L", "LTR": "L",
    "LITER": "L", "LITRE": "L", "ML": "ML", "M": "M", "MTR": "M", "METER": "M", "METRE": "M",
    "CM": "CM", "MM": "MM", "M2": "M2", "SQM": "M2", "M3": "M3", "CBM": "M3", "H": "H", "HR": "H",
    "HUR": "H", "HOUR": "H", "D": "DAY", "DAY": "DAY", "SET": "SET", "BOX": "BOX", "PAL": "PAL",
}  # fmt: skip

LANGUAGES = {
    "E": "EN", "EN": "EN", "ENG": "EN", "ENGLISH": "EN", "D": "DE", "DE": "DE", "DEU": "DE",
    "GER": "DE", "GERMAN": "DE", "DEUTSCH": "DE", "F": "FR", "FR": "FR", "FRA": "FR",
    "FRENCH": "FR", "S": "ES", "ES": "ES", "SPA": "ES", "SPANISH": "ES", "I": "IT", "IT": "IT",
    "ITA": "IT", "ITALIAN": "IT", "P": "PT", "PT": "PT", "POR": "PT", "PORTUGUESE": "PT",
    "NL": "NL", "DUTCH": "NL", "PL": "PL", "POLISH": "PL", "SV": "SV", "SWEDISH": "SV",
}  # fmt: skip


def _resolve_currency(value: str) -> str | None:
    key = value.strip().upper()
    return key if key in CURRENCIES else ({"EURO": "EUR"}.get(key))


@dataclass(frozen=True)
class Domain:
    """A kind of coded value that is typically translated through a value list."""

    key: str  # also the suggested XREF name
    title: str
    field_names: re.Pattern[str]
    resolve: Callable[[str], str | None]
    universe: Callable[[], list[tuple[str, str]]]  # rows offered when no data is available
    note: str = ""
    words: tuple[str, ...] = field(default_factory=tuple)  # words that mark it in a description


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


DOMAINS: list[Domain] = [
    Domain(
        "country",
        "country",
        _re(r"^(COUNTRY|LAND1|LAND|CTRY|CNTRY|BANKS|POBOX_CTRY|NATION|.*_COUNTRY)$"),
        resolve_country,
        lambda: [(c, c) for c in sorted(ALPHA2)],
        "Values are recognised as ISO 3166 country codes, three-letter codes, English names and "
        "common spellings such as UK.",
    ),
    Domain(
        "currency",
        "currency",
        _re(r"^(WAERS|CURRENCY|CURR|CUKY|.*_CURRENCY|.*_WAERS|WAERS_.*)$"),
        _resolve_currency,
        lambda: [(c, c) for c in sorted(CURRENCIES)],
        "Values are recognised as ISO 4217 currency codes.",
    ),
    Domain(
        "uom",
        "unit of measure",
        _re(r"^(MEINS|UOM|UNIT|BASE_UOM|.*_UOM|MSEHI|MEINH|BSTME)$"),
        lambda v: UOM.get(v.strip().upper()),
        lambda: sorted(UOM.items()),
        "Suggested target values are common ISO unit codes. Check them against the units "
        "configured in your target system.",
    ),
    Domain(
        "language",
        "language",
        _re(r"^(SPRAS|LANGU|LANG|LANGUAGE|LANGU_CORR|LANG_CORR_PERS|LANGU_ISO|.*_LANGU)$"),
        lambda v: LANGUAGES.get(v.strip().upper()),
        lambda: sorted(LANGUAGES.items()),
        "Suggested target values are two-letter ISO 639-1 codes. Check the format your target "
        "system expects.",
    ),
]


# Alternative names legacy systems use for common target fields (all lower case, no separators).
# The key is the target field name, squashed the same way. Heuristics, offered as proposals.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "lifnr": ("vendor", "vendorid", "vendornumber", "vendorno", "supplier", "supplierid", "supplierno", "suppliernumber"),
    "namefirst": ("name1", "name", "vendorname", "suppliername"),
    "country": ("land1", "land", "ctry", "cntry", "countrycode"),
    "city1": ("ort01", "city", "town"),
    "postcode1": ("pstlz", "postcode", "postalcode", "zip", "zipcode"),
    "street": ("stras", "street1", "streetname", "addressline1"),
    "telnrlong": ("telf1", "phone", "phonenumber", "tel", "telephone"),
    "faxnrlong": ("telfx", "fax", "faxnumber"),
    "smtpaddr": ("email", "emailaddress", "mail"),
    "langucorr": ("spras", "language", "lang"),
    "region": ("regio", "state", "province"),
    "sortl": ("matchcode", "searchterm"),
}  # fmt: skip


def domain_for_field(name: str) -> Domain | None:
    return next((d for d in DOMAINS if d.field_names.match(name)), None)


@dataclass(frozen=True)
class CheckRule:
    """A check worth proposing for fields whose name matches."""

    key: str
    field_names: re.Pattern[str]
    type: str
    settings: dict[str, object]
    severity: str
    why: str
    confidence: str = "high"


NAME_CHECKS: list[CheckRule] = [
    CheckRule(
        "country_format", _re(r"^(COUNTRY|LAND1|LAND|CTRY|CNTRY|BANKS|POBOX_CTRY|NATION|.*_COUNTRY)$"),
        "PATTERN", {"pattern": "[A-Z]{2}"}, "error",
        "Country codes are two capital letters (ISO 3166).",
    ),
    CheckRule(
        "currency_format", _re(r"^(WAERS|CURRENCY|CURR|CUKY|.*_CURRENCY|.*_WAERS|WAERS_.*)$"),
        "PATTERN", {"pattern": "[A-Z]{3}"}, "error",
        "Currency codes are three capital letters (ISO 4217).",
    ),
    CheckRule(
        "email_format", _re(r"^(SMTP_ADDR.*|E_?MAIL.*|EMAIL.*|MAIL_ADDR.*)$"),
        "PATTERN", {"pattern": r"[^@\s]+@[^@\s]+\.[^@\s]+"}, "warn",
        "An e-mail address needs an @ and a domain.",
    ),
    CheckRule(
        "iban_format", _re(r"^(IBAN.*)$"),
        "PATTERN", {"pattern": "[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}"}, "warn",
        "An IBAN starts with a two-letter country code and two check digits.",
    ),
    CheckRule(
        "phone_format", _re(r"^(TEL.*|MOBILE.*|FAX.*|PHONE.*)$"),
        "PATTERN", {"pattern": r"[+0-9 ()/.\-]+"}, "warn",
        "Phone numbers use digits, spaces and + ( ) / . - only.",
        "medium",
    ),
    CheckRule(
        "company_code_format", _re(r"^(BUKRS)$"),
        "PATTERN", {"pattern": "[A-Za-z0-9]{4}"}, "warn",
        "Company codes are four letters or digits.", "medium",
    ),
]  # fmt: skip
