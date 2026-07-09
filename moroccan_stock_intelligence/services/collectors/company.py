"""Company-profile persister (thin): turns a parsed IssuerPage into one row.

`business_model` stays NULL: no business-model narrative is published, and we never
synthesise one. `management_json` is populated from the `Dirigeants` slide grid
(layout confirmed on ATW/LBV/IAM, 2026-07-09).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from moroccan_stock_intelligence.repository import upsert_company_profile
from moroccan_stock_intelligence.services.collectors import OFFICIAL_SOURCE
from moroccan_stock_intelligence.services.collectors.issuer_page import IssuerPage

LOG = logging.getLogger(__name__)


def persist_profile(session: Session, stock_id: int, page: IssuerPage) -> bool:
    """Upsert the profile. Returns False when the page carried nothing usable."""
    if not page.profile and not page.ownership and not page.management:
        return False
    fields = {
        "emetteur_code": page.emetteur_code,
        "emetteur_url": page.emetteur_url,
        "company_name": page.profile.get("company_name"),
        "description": page.profile.get("description"),
        "business_model": None,  # not published — never synthesised
        "siege_social": page.profile.get("siege_social"),
        "commissaire_aux_comptes": page.profile.get("commissaire_aux_comptes"),
        "date_constitution": page.profile.get("date_constitution"),
        "date_introduction": page.profile.get("date_introduction"),
        "duree_exercice_social": page.profile.get("duree_exercice_social"),
        "ownership_json": json.dumps(page.ownership, ensure_ascii=False) if page.ownership else None,
        "management_json": json.dumps(page.management, ensure_ascii=False) if page.management else None,
        "source": OFFICIAL_SOURCE,
        "source_url": page.emetteur_url,
        "raw_payload": json.dumps(page.profile, ensure_ascii=False) if page.profile else None,
    }
    upsert_company_profile(session, stock_id, fields)
    LOG.info(
        "profile_stored symbol=%s holders=%s dirigeants=%s",
        page.symbol, len(page.ownership), len(page.management),
    )
    return True
