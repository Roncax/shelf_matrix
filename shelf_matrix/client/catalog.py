"""Catalog of programs found under the programs directory."""
from __future__ import annotations

import logging
from pathlib import Path

from shelf_matrix.schema import Manifest, load_manifest

logger = logging.getLogger(__name__)


def load_catalog(programs_dir: Path) -> dict[str, Manifest]:
    catalog: dict[str, Manifest] = {}
    for d in sorted(Path(programs_dir).iterdir()):
        if not (d / "manifest.toml").is_file():
            continue
        try:
            m = load_manifest(d)
        except Exception as e:  # one broken program must not stop the client
            logger.warning("skipping invalid manifest in %s: %s", d.name, e)
            continue
        if m.id in catalog:
            logger.warning("duplicate program id %r in %s; keeping %s",
                           m.id, d.name, catalog[m.id].path.name)
            continue
        catalog[m.id] = m
    return catalog
