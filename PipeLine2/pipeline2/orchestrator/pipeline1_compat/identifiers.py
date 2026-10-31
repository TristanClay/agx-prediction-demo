# Migrated from Pipeline1 without modifying its source.
import re
import unicodedata

BUCKET_ALIASES = {
    "baseline": "ag_baseline",
    "Ag baseline": "ag_baseline",
    "Ag-M-OH interface": "OH_affinity_site",
    "Ag-M / Ag-M-Ox bifunctional interface": "metal_oxide_interface",
    "furoate/carboxylate poisoning and desorption": "coverage_induced_poisoning",
    "HER/bubble-assisted regeneration": "cathodic_HER_regeneration",
}


def stable_slug(value: str, max_length: int = 72) -> str:
    text = str(value or "").strip()
    text = text.replace("Δ", "DG_").replace("*", "_STAR_").replace("+", "_PLUS_")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return (text[:max_length] or "UNKNOWN").strip("_")


def canonical_bucket(value: str) -> str:
    raw = str(value or "").strip()
    return BUCKET_ALIASES.get(raw, raw)


def mechanism_bucket_id(bucket: str) -> str:
    return f"MB_{stable_slug(canonical_bucket(bucket), 56)}_V1"


def element_candidate_id(bucket: str, element: str) -> str:
    canonical = canonical_bucket(bucket)
    if canonical == "ag_baseline" and str(element).strip() == "Ag":
        return "EC_AG_BASELINE_V1"
    return f"EC_{stable_slug(canonical, 48)}_{stable_slug(element, 12)}_V1"
