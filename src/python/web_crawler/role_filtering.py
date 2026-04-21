from __future__ import annotations

import logging

from bson import ObjectId
from bson.errors import InvalidId


def load_identity_roles(
    identities_collection,
    identity_id: str,
    *,
    logger: logging.Logger,
    workflow_name: str,
) -> list[str]:
    """Load trimmed identity roles from MongoDB. Returns an empty list on lookup errors."""
    if not identity_id:
        logger.warning("%s: identity_id is empty; no jobs will be extracted", workflow_name)
        return []

    try:
        identity_oid = ObjectId(identity_id)
    except (InvalidId, TypeError):
        logger.warning("%s: invalid identity_id %r; no jobs will be extracted", workflow_name, identity_id)
        return []

    try:
        identity = identities_collection.find_one({"_id": identity_oid})
    except Exception as exc:
        logger.exception("%s: failed to load identity %s: %s", workflow_name, identity_id, exc)
        return []

    if identity is None:
        logger.warning("%s: identity %s not found; no jobs will be extracted", workflow_name, identity_id)
        return []

    roles = [role.strip() for role in identity.get("roles", []) if isinstance(role, str) and role.strip()]
    logger.debug("%s: loaded %d roles for identity %s: %s", workflow_name, len(roles), identity_id, roles)
    return roles


def text_matches_roles(title: str, description: str, roles: list[str]) -> bool:
    """Return True when any role keyword appears in title or description."""
    if not roles:
        return False

    title_lower = (title or "").lower()
    description_lower = (description or "").lower()
    for role in roles:
        role_lower = role.lower()
        if role_lower in title_lower or role_lower in description_lower:
            return True
    return False
