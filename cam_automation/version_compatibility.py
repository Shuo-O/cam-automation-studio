from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


VersionTier = Literal[
    "verified",
    "review_required",
    "opaque_only",
    "unsupported",
    "unknown",
]

_VERSION_NUMBER = re.compile(r"\d+(?:\.\d+)*")
_VERSION_COMPARATOR = re.compile(r"(>=|<=|>|<|==|=)\s*(\d+(?:\.\d+)*)")
_CANONICAL_PRODUCT_NAMES = {
    "nx": "NX",
    "powermill": "PowerMill",
    "cimatron": "Cimatron",
}
_DEFAULT_PRODUCT_ALIASES = {
    "nx": ("NX", "Siemens NX", "UG NX"),
    "powermill": (
        "PowerMill",
        "PowerMill Ultimate",
        "Autodesk PowerMill",
        "Autodesk PowerMill Ultimate",
        "PowerMILL",
    ),
    "cimatron": ("Cimatron", "Cimatron E"),
}
_POLICY_LIST_FIELDS = (
    "aliases",
    "review_required_ranges",
    "opaque_only_ranges",
    "unsupported_ranges",
)


@dataclass(frozen=True, slots=True)
class TargetVersionClassification:
    product: str
    input_version: str
    normalized_version: str
    tier: VersionTier
    matched_range: str | None
    matched_version: str | None
    policy_source: str

    @property
    def preview_supported(self) -> bool:
        return self.tier == "verified"

    @property
    def semantic_edit_supported(self) -> bool:
        return self.tier in {"verified", "review_required"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "input_version": self.input_version,
            "normalized_version": self.normalized_version,
            "tier": self.tier,
            "matched_range": self.matched_range,
            "matched_version": self.matched_version,
            "policy_source": self.policy_source,
            "preview_supported": self.preview_supported,
            "semantic_edit_supported": self.semantic_edit_supported,
        }


def _version_tuple(value: str) -> tuple[int, ...] | None:
    match = _VERSION_NUMBER.search(value)
    if match is None:
        return None
    return tuple(int(item) for item in match.group(0).split("."))


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    normalized_left = left + (0,) * (width - len(left))
    normalized_right = right + (0,) * (width - len(right))
    return (normalized_left > normalized_right) - (
        normalized_left < normalized_right
    )


def target_version_matches(target_version: str, declared_range: str) -> bool:
    """Conservative product-neutral exact, wildcard, and comparator matching."""

    target_version = target_version.strip()
    declared_range = declared_range.strip()
    if not target_version or not declared_range:
        return False
    if target_version.casefold() == declared_range.casefold():
        return True
    if declared_range.endswith("*"):
        return target_version.casefold().startswith(
            declared_range[:-1].strip().casefold()
        )
    comparators = _VERSION_COMPARATOR.findall(declared_range)
    if not comparators:
        return False
    target_number = _version_tuple(target_version)
    if target_number is None:
        return False
    for operator, raw_version in comparators:
        required = tuple(int(item) for item in raw_version.split("."))
        comparison = _compare_versions(target_number, required)
        if operator == ">=" and comparison < 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator in {"=", "=="} and comparison != 0:
            return False
    match = _VERSION_COMPARATOR.search(declared_range)
    if match is None:
        return False
    prefix = declared_range[: match.start()].strip()
    if prefix and not target_version.casefold().startswith(prefix.casefold()):
        return False
    return True


def _version_policy(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    extensions = manifest.get("extensions")
    if not isinstance(extensions, Mapping):
        return {}
    cam_automation = extensions.get("cam_automation")
    if not isinstance(cam_automation, Mapping):
        return {}
    policy = cam_automation.get("version_compatibility")
    return policy if isinstance(policy, Mapping) else {}


def validate_version_compatibility_policy(manifest: Mapping[str, Any]) -> None:
    """Validate only the optional policy consumed by deterministic classifiers."""

    policy = _version_policy(manifest)
    if not policy:
        return
    if policy.get("policy_version") != 1:
        raise ValueError("version_compatibility.policy_version must be 1.")
    for field in _POLICY_LIST_FIELDS:
        value = policy.get(field, [])
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise ValueError(
                f"version_compatibility.{field} must be an array of non-empty strings."
            )


def normalize_target_version(
    target_version: str,
    product: str,
    *,
    aliases: Sequence[str] = (),
) -> str:
    """Normalize product aliases without inventing or translating version numbers."""

    value = str(target_version).strip()
    canonical = _CANONICAL_PRODUCT_NAMES.get(product)
    if not value or canonical is None:
        return value
    candidates = {
        *(_DEFAULT_PRODUCT_ALIASES.get(product, ())),
        *(str(item).strip() for item in aliases if str(item).strip()),
    }
    for alias in sorted(candidates, key=lambda item: (-len(item), item.casefold())):
        if not value.casefold().startswith(alias.casefold()):
            continue
        remainder = value[len(alias) :]
        if remainder and remainder[0].isalpha():
            continue
        remainder = remainder.strip(" \t-_:")
        return canonical if not remainder else f"{canonical} {remainder}"
    return value


def _release_candidate(target_version: str, product: str) -> str | None:
    canonical = _CANONICAL_PRODUCT_NAMES.get(product)
    version = _VERSION_NUMBER.search(target_version)
    if canonical is None or version is None:
        return None
    parts = tuple(int(item) for item in version.group(0).split("."))
    major = parts[0]
    if product == "nx" and major >= 1847:
        return f"{canonical} {major}"
    if product == "powermill" and major >= 2000:
        return f"{canonical} {major}"
    if product == "cimatron" and major >= 2000:
        return f"{canonical} {major}"
    return None


def _first_matching_range(
    target_versions: Sequence[str],
    ranges: Sequence[Any],
) -> tuple[str, str] | None:
    for value in ranges:
        declared_range = str(value).strip()
        if not declared_range:
            continue
        for target_version in target_versions:
            if target_version_matches(target_version, declared_range):
                return declared_range, target_version
    return None


def classify_target_version(
    target_version: str,
    manifest: Mapping[str, Any],
) -> TargetVersionClassification:
    """Classify a target without broadening verified capability claims."""

    product = str(manifest.get("product", "")).strip()
    input_version = str(target_version).strip()
    policy = _version_policy(manifest)
    aliases = policy.get("aliases", [])
    normalized = normalize_target_version(
        input_version,
        product,
        aliases=aliases if isinstance(aliases, (list, tuple)) else (),
    )
    if not input_version or _version_tuple(normalized) is None:
        return TargetVersionClassification(
            product=product,
            input_version=input_version,
            normalized_version=normalized,
            tier="unknown",
            matched_range=None,
            matched_version=None,
            policy_source="none",
        )

    release_candidate = _release_candidate(normalized, product)
    matching_versions = tuple(
        dict.fromkeys(
            item
            for item in (normalized, release_candidate)
            if item
        )
    )
    verified = _first_matching_range(
        matching_versions,
        manifest.get("target_version_ranges", []),
    )
    if verified is not None:
        return TargetVersionClassification(
            product=product,
            input_version=input_version,
            normalized_version=normalized,
            tier="verified",
            matched_range=verified[0],
            matched_version=verified[1],
            policy_source="target_version_ranges",
        )

    for field, tier in (
        ("review_required_ranges", "review_required"),
        ("opaque_only_ranges", "opaque_only"),
        ("unsupported_ranges", "unsupported"),
    ):
        ranges = policy.get(field, [])
        matched = _first_matching_range(
            matching_versions,
            ranges if isinstance(ranges, (list, tuple)) else (),
        )
        if matched is not None:
            return TargetVersionClassification(
                product=product,
                input_version=input_version,
                normalized_version=normalized,
                tier=tier,
                matched_range=matched[0],
                matched_version=matched[1],
                policy_source=f"extensions.cam_automation.version_compatibility.{field}",
            )

    return TargetVersionClassification(
        product=product,
        input_version=input_version,
        normalized_version=normalized,
        tier="unsupported",
        matched_range=None,
        matched_version=None,
        policy_source="none",
    )


__all__ = [
    "TargetVersionClassification",
    "VersionTier",
    "classify_target_version",
    "normalize_target_version",
    "target_version_matches",
    "validate_version_compatibility_policy",
]
