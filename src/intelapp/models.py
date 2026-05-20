from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


CSV_FIELDS = [
    "listing_id",
    "listing_url",
    "property_address",
    "suburb",
    "postcode",
    "weekly_rent",
    "bedrooms",
    "bathrooms",
    "parking",
    "date_listed",
    "owner_name",
    "owner_postal_address",
    "owner_address_matches_property",
    "lookup_status",
    "rpdata_url",
]


@dataclass
class Listing:
    listing_id: str
    listing_url: str
    property_address: str
    suburb: str
    postcode: str
    weekly_rent: Optional[int]
    bedrooms: Optional[int]
    bathrooms: Optional[int]
    parking: Optional[int]
    date_listed: str = ""


@dataclass
class OwnerInfo:
    owner_name: str = ""
    owner_postal_address: str = ""
    rpdata_url: str = ""
    lookup_status: str = "pending"


@dataclass
class Row:
    listing: Listing
    owner: OwnerInfo = field(default_factory=OwnerInfo)

    def to_csv_dict(self) -> dict:
        prop_norm = _normalise(self.listing.property_address)
        owner_norm = _normalise(self.owner.owner_postal_address)
        matches = ""
        if self.owner.owner_postal_address:
            matches = "true" if prop_norm and prop_norm == owner_norm else "false"

        d = asdict(self.listing)
        d.update(asdict(self.owner))
        d["owner_address_matches_property"] = matches
        return {k: d.get(k, "") for k in CSV_FIELDS}


def _normalise(addr: str) -> str:
    if not addr:
        return ""
    s = addr.lower().strip()
    # Drop unit / lot prefixes ("1/23 Smith St" -> "23 smith st")
    if "/" in s:
        s = s.split("/", 1)[1]
    # Collapse whitespace and strip commas
    s = " ".join(s.replace(",", " ").split())
    return s
