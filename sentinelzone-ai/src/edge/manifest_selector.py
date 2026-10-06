"""Resolve the shift manifest a camera should be governed by.

The original repository disagrees with itself about the site identity:

* ``config/default_config.json`` declares ``site_id: "SITE-5-EARTHMOVING"``
  with ``camera_id: "CAM-02-MAST"``;
* ``data/manifests/active_manifest.json`` is the ``SITE-EAST`` shift, and
  ``SHIFT_2026-09-07_SITE-5.json`` is the ``SITE_5`` shift.

The live path originally loaded envelopes only on an exact string match between
the configured site and the manifest site, so a live camera silently ended up
with **no hazard zones at all**. These helpers keep the original settings
untouched and make the lookup tolerant instead, while always recording which
manifest actually supplied the envelopes so a safety event can report a site
that agrees with its own zone.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

#: Resolved from the repository root, so the manifest is found regardless of
#: the directory the application was started from.
REPO_ROOT = Path(__file__).resolve().parents[2]
#: The checked-in seed location. ``SENTINEL_MANIFEST_DIR`` overrides it, which is
#: what lets a deployment - and the test suite - keep compiled manifests somewhere
#: other than the repository's own configuration.
MANIFEST_DIR = REPO_ROOT / "data" / "manifests"
ACTIVE_MANIFEST = MANIFEST_DIR / "active_manifest.json"


def manifest_dir() -> Path:
    """The directory manifests are read from and written to.

    Resolved on every call so that a caller which sets ``SENTINEL_MANIFEST_DIR``
    is obeyed, including a test that does not want to touch the repository.
    """
    override = os.getenv("SENTINEL_MANIFEST_DIR")
    return Path(override) if override else MANIFEST_DIR


def active_manifest_path() -> Path:
    return manifest_dir() / "active_manifest.json"


def normalise_site(site_id: Optional[str]) -> str:
    """Reduce a site identifier to a comparable form.

    ``SITE-5-EARTHMOVING``, ``SITE_5`` and ``site-east`` become ``site5``,
    ``site5`` and ``siteeast``. Separators and case carry no meaning between
    these identifiers, so they must not decide whether zones are loaded.
    """
    if not site_id:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(site_id).lower())


def _iter_manifests(directory: Path):
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            yield path, json.loads(path.read_text())
        except (OSError, ValueError):
            continue


def resolve_manifest(
    site_id: Optional[str],
    directory: Optional[Path] = None,
    active: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the shift manifest that governs ``site_id``.

    Preference order: an exact site match, then a normalised site match, then
    the active manifest. Returns the manifest payload plus the path it came
    from, or ``None`` when nothing is available.
    """
    # Resolved here rather than in the signature: a default argument is evaluated
    # once at import, so it would ignore SENTINEL_MANIFEST_DIR set later.
    if directory is None:
        directory = manifest_dir()
    if active is None:
        active = active_manifest_path()
    target = normalise_site(site_id)
    exact: Optional[Dict[str, Any]] = None
    normalised: Optional[Dict[str, Any]] = None

    for path, payload in _iter_manifests(directory):
        manifest_site = payload.get("site_id")
        if site_id and manifest_site == site_id:
            exact = {"path": path, "manifest": payload, "match": "exact"}
        elif target and normalise_site(manifest_site) == target:
            normalised = {"path": path, "manifest": payload, "match": "normalised"}

    for candidate in (exact, normalised):
        if candidate is not None:
            return candidate

    if active.is_file():
        try:
            payload = json.loads(active.read_text())
            return {"path": active, "manifest": payload, "match": "active_fallback"}
        except (OSError, ValueError):
            return None
    return None
