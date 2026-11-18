"""Adapters for live systems.

The contract, capabilities, credentials and target endpoints are defined here. The HTTP calls
themselves are added once each one can be tested against a sandbox (SAP Business Accelerator
Hub, a Salesforce Developer Edition org, a NetSuite account); until then a configured adapter
reports that live calls are not enabled yet instead of guessing at an API.
"""

from ..config import Settings
from .base import (
    FINANCIALS,
    JOURNAL_ENTRIES,
    PURCHASE_ORDERS,
    SKUS,
    Connector,
    ConnectorError,
    JournalEntryPayload,
    PurchaseOrderPayload,
)


class _LiveConnector(Connector):
    required: dict[str, str] = {}  # settings field -> env var name

    def __init__(self, settings: Settings):
        self.settings = settings

    def is_configured(self) -> bool:
        return all(getattr(self.settings, field) for field in self.required)

    def missing_config_hint(self) -> str | None:
        missing = [env for field, env in self.required.items() if not getattr(self.settings, field)]
        return f"Set {', '.join(missing)} in backend/.env." if missing else None

    def _not_enabled(self, endpoint: str) -> ConnectorError:
        return ConnectorError(f"Live calls to {self.name} ({endpoint}) are not enabled yet.")

    def fetch_financials(self, start, end):
        self.require_configured()
        raise self._not_enabled(self.financials_endpoint)

    def fetch_skus(self):
        self.require_configured()
        raise self._not_enabled(self.skus_endpoint)

    financials_endpoint = ""
    skus_endpoint = ""


class SapS4Hana(_LiveConnector):
    id = "sap_s4hana"
    name = "SAP S/4HANA"
    kind = "ERP"
    description = "Financials, stock, purchase orders, and journal entries via SAP OData/SOAP APIs."
    capabilities = frozenset({FINANCIALS, SKUS, PURCHASE_ORDERS, JOURNAL_ENTRIES})
    required = {"sap_base_url": "PROSIGHT_SAP_BASE_URL", "sap_api_key": "PROSIGHT_SAP_API_KEY"}
    financials_endpoint = "API_JOURNALENTRYITEMBASIC_SRV"
    skus_endpoint = "API_PRODUCT_SRV + API_MATERIAL_STOCK_SRV"

    def create_purchase_order(self, po: PurchaseOrderPayload) -> str:
        self.require_configured()
        raise self._not_enabled("API_PURCHASEORDER_PROCESS_SRV POST A_PurchaseOrder")

    def post_journal_entry(self, entry: JournalEntryPayload) -> str:
        self.require_configured()
        raise self._not_enabled("JournalEntryCreateRequestConfirmation_In")


class SalesforceSalesCloud(_LiveConnector):
    """Sales Cloud is lead-to-cash (sell side): it has no general ledger and no standard vendor-PO
    object. Sales trends come from Orders/Opportunities; POs are raised into a custom
    Purchase_Order__c object. It cannot be the ABOR."""

    id = "salesforce"
    name = "Salesforce Sales Cloud"
    kind = "CRM"
    description = "Sales orders and products; raises POs into a Purchase_Order__c custom object."
    capabilities = frozenset({FINANCIALS, SKUS, PURCHASE_ORDERS})
    required = {
        "salesforce_instance_url": "PROSIGHT_SALESFORCE_INSTANCE_URL",
        "salesforce_access_token": "PROSIGHT_SALESFORCE_ACCESS_TOKEN",
    }
    financials_endpoint = "SOQL on Order/OrderItem (revenue only; no COGS)"
    skus_endpoint = "SOQL on Product2/PricebookEntry"

    def create_purchase_order(self, po: PurchaseOrderPayload) -> str:
        self.require_configured()
        raise self._not_enabled("POST /services/data/vXX.X/sobjects/Purchase_Order__c")


class OracleNetSuite(_LiveConnector):
    id = "oracle_netsuite"
    name = "Oracle NetSuite"
    kind = "ERP"
    description = "Financials, items, purchase orders, and journal entries via SuiteTalk REST."
    capabilities = frozenset({FINANCIALS, SKUS, PURCHASE_ORDERS, JOURNAL_ENTRIES})
    required = {
        "netsuite_account_id": "PROSIGHT_NETSUITE_ACCOUNT_ID",
        "netsuite_access_token": "PROSIGHT_NETSUITE_ACCESS_TOKEN",
    }
    financials_endpoint = "SuiteQL on transactionAccountingLine"
    skus_endpoint = "REST record/v1/inventoryItem"

    def create_purchase_order(self, po: PurchaseOrderPayload) -> str:
        self.require_configured()
        raise self._not_enabled("REST record/v1/purchaseOrder")

    def post_journal_entry(self, entry: JournalEntryPayload) -> str:
        self.require_configured()
        raise self._not_enabled("REST record/v1/journalEntry")
