"""Connector registry and routing.

Two routing settings decide where approved decisions go:
  abor_connector  receives journal entries (the account book of record)
  po_connector    receives new purchase orders
Both default to the built-in demo ERP.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import settings
from ..models import ConnectorState, Setting
from .base import JOURNAL_ENTRIES, PURCHASE_ORDERS, Connector, ConnectorError
from .demo import DemoErp
from .external import OracleNetSuite, SalesforceSalesCloud, SapS4Hana

ROUTES = {"abor_connector": JOURNAL_ENTRIES, "po_connector": PURCHASE_ORDERS}
DEFAULT_CONNECTOR = DemoErp.id


def all_connectors(db: Session) -> list[Connector]:
    return [DemoErp(db), SapS4Hana(settings), SalesforceSalesCloud(settings), OracleNetSuite(settings)]


def get_connector(db: Session, connector_id: str) -> Connector | None:
    return next((c for c in all_connectors(db) if c.id == connector_id), None)


def is_connected(db: Session, connector_id: str) -> bool:
    state = db.get(ConnectorState, connector_id)
    return bool(state and state.connected)


def set_connected(db: Session, connector: Connector, connected: bool) -> None:
    if connected:
        connector.require_configured()
    state = db.get(ConnectorState, connector.id) or ConnectorState(id=connector.id)
    state.connected = connected
    state.connected_at = datetime.now(UTC) if connected else None
    db.add(state)


def route(db: Session, key: str) -> str:
    setting = db.get(Setting, key)
    return setting.value if setting else DEFAULT_CONNECTOR


def set_route(db: Session, key: str, connector_id: str) -> None:
    connector = get_connector(db, connector_id)
    if connector is None:
        raise ConnectorError(f"Unknown connector '{connector_id}'.")
    if ROUTES[key] not in connector.capabilities:
        purpose = "account book of record" if key == "abor_connector" else "purchase order target"
        raise ConnectorError(f"{connector.name} cannot be the {purpose}.")
    if not is_connected(db, connector_id):
        raise ConnectorError(f"Connect {connector.name} first.")
    setting = db.get(Setting, key) or Setting(key=key)
    setting.value = connector_id
    db.add(setting)


def routed_connector(db: Session, key: str) -> Connector:
    """The connected connector that should receive this kind of posting."""
    connector = get_connector(db, route(db, key))
    if connector is None or not is_connected(db, connector.id):
        raise ConnectorError("The target system for this posting is not connected.")
    return connector
