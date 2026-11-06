"""Fixed vocabularies for the spine. Must match migrations/001_spine.sql."""

CONCEPT_TYPES = {
    "Condition", "Test", "Threshold", "Population", "RiskFactor",
    "Intervention", "DrugClass", "Complication", "Outcome", "Rule",
}

# concept id prefix -> required concept type
ID_PREFIX_TYPE = {
    "cond": "Condition", "test": "Test", "thr": "Threshold", "pop": "Population",
    "rf": "RiskFactor", "int": "Intervention", "drug": "DrugClass",
    "comp": "Complication", "outcome": "Outcome", "rule": "Rule",
}

# predicate -> requires a numeric value
PREDICATES = {
    "classified_as": False,
    "subtype_of": False,
    "diagnosed_by": False,
    "has_threshold": True,
    "requires_confirmation": False,
    "unreliable_when": False,
    "use_instead": False,
    "screen_with": False,
    "screening_starts_at": True,
    "repeat_interval": True,
    "risk_factor_for": False,
    "defined_by": False,
    # added for ADA Section 3 (prevention), 2026-11-06
    "prevented_or_delayed_by": False,
    "has_target": True,
    "requires_monitoring_of": False,
}

OPERATORS = {">=", ">", "<=", "<", "=", "range"}
STATUSES = {"draft", "proposed", "accepted", "rejected", "superseded"}
