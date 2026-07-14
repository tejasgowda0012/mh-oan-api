"""

Cross-network scheme status

"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from helpers.utils import get_logger
import httpx
from pydantic import BaseModel, AnyHttpUrl, Field
from typing import List, Optional, Dict, Any, ClassVar
from pydantic_ai import ModelRetry, UnexpectedModelBehavior, RunContext
from agents.deps import FarmerContext
from dotenv import load_dotenv
from langfuse import observe

load_dotenv()

logger = get_logger(__name__)

# -----------------------
# Basic Models
# -----------------------
class Descriptor(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    short_desc: Optional[str] = None
    long_desc: Optional[str] = None

    def __str__(self) -> str:
        if self.name and self.name != "NA":
            return self.name
        elif self.code:
            return self.code
        return "Unknown Scheme"

# -----------------------
# Tag Models
# -----------------------
class Tag(BaseModel):
    code: Optional[str] = None
    descriptor: Optional[Descriptor] = None
    value: str

    def __str__(self) -> str:
        if self.code:
            return f"{self.code}: {self.value}"
        elif self.descriptor and self.descriptor.code:
            return f"{self.descriptor.code}: {self.value}"
        return self.value

# -----------------------
# Scheme Application Models
# -----------------------
class SchemeApplication(BaseModel):
    """Model representing a farmer's scheme application with status and details."""

    id: str
    descriptor: Descriptor
    tags: List[Tag]

    # Class-level PII codes that should be masked
    PII_CODES: ClassVar[set[str]] = {"application_id", "farmer_component_mapping_id"}
    
    # Status labels with emojis and Marathi translations
    STATUS_LABELS: ClassVar[Dict[str, str]] = {
        "Fund Disbursed": "✅ Fund Disbursed (पैसे दिले गेले)",
        "Winner": "🏆 Winner (निवड झाली)",
        "Wait List": "⏳ Wait List (प्रतीक्षा यादीत आहे)",
        "WaitList": "⏳ Wait List (प्रतीक्षा यादीत आहे)",
        "Application cancelled by applicant": "❌ Cancelled by Applicant (तुम्ही अर्ज रद्द केला)",
        "Department Cancelled": "🚫 Department Cancelled (विभागाने अर्ज रद्द केला)",
        "Approved": "✅ Approved (अर्ज मंजूर झाला)",
        "Rejected": "❌ Rejected (अर्ज नाकारला)",
        "Under Review": "📋 Under Review (अर्ज तपासणीमध्ये आहे)",
        "Pending": "⏳ Pending (अर्ज थांबलेला आहे)",
        "Upload Documents": "📄 Upload Documents (कागदपत्रे टाका)",
        "Document scrutiny before pre-sanction": "🔍 Document scrutiny before pre-sanction (पूर्वमंजुरीसाठी कागदपत्र पडताळणी)",
        "Document SLA Cancelled": "🚫 Document SLA Cancelled (वेळेत कागदपत्रे न दिल्यामुळे रद्द)",
        "Upload DPR": "📝 Upload DPR (डीपीआर टाका)",
        "Document Scrutiny and Upload Site Inspection": "🔍 Document Check & Site Inspection Upload (कागदपत्र तपासणी व जागेची पाहणी अपलोड)",
        "Application Approved and Sanction letter generated": "✅ Application Approved – Sanction Letter Ready (अर्ज मंजूर – मंजुरी पत्र तयार आहे)",
        "Document Scrutiny and Upload Site Inspection": "🔍 Document Check & Site Inspection Upload (कागदपत्र तपासणी व जागेची पाहणी अपलोड)",
        "Application Cancelled (Give Up Subsidy)": "🚫 Application Cancelled – Subsidy Given Up (अर्ज रद्द – अनुदानाचा त्याग केला)",        
        "Application rejected by department": "❌ Application Rejected by Department (विभागाने अर्ज नाकारला)",
        "Upload Invoice": "🧾 Upload Invoice (बिल अपलोड करा)",
    }

    @classmethod
    def add_pii_code(cls, code: str) -> None:
        """Add a new code to the PII codes list.

        Args:
            code: The tag code to be masked as PII
        """
        cls.PII_CODES.add(code)

    @classmethod
    def remove_pii_code(cls, code: str) -> None:
        """Remove a code from the PII codes list.

        Args:
            code: The tag code to remove from PII masking
        """
        cls.PII_CODES.discard(code)

    @classmethod
    def get_pii_codes(cls) -> set[str]:
        """Get a copy of the current PII codes.

        Returns:
            A set of PII codes that are currently being masked
        """
        return cls.PII_CODES.copy()

    @classmethod
    def format_status_display(cls, status: str) -> str:
        """Format status with appropriate emoji indicators and Marathi translations.
        
        Args:
            status: The status string to format
            
        Returns:
            Formatted status string with emoji and translation
        """
        return cls.STATUS_LABELS.get(status, f"📄 {status}")

    def _get_tag_value(self, code: str) -> Optional[str]:
        """Get value for a specific tag code."""
        for tag in self.tags:
            if tag.code == code or (tag.descriptor and tag.descriptor.code == code):
                if tag.value in ["null", "NA"]:
                    return None
                return tag.value
        return None

    def _format_tag_code(self, code: str) -> str:
        """Convert tag code to human-readable label."""
        special_mappings = {
            "application_id": "Application ID",
            "farmer_component_mapping_id": "Component Mapping ID",
            "status": "Status",
            "status_details": "Status Details",
            "primary_scheme": "Primary Scheme",
            "top_scheme": "Top Scheme",
            "last_updated_date": "Last Updated",
            "component_updated_date": "Component Updated",
            "disbursement_date": "Disbursement Date",
            "instalment_number": "Instalment Number",
            "instalment_status": "Instalment Status",
            "financial_year": "Financial Year",
        }

        if code in special_mappings:
            return special_mappings[code]

        # Generic conversion: split by underscore, capitalize each word
        return " ".join(word.capitalize() for word in code.split("_"))

    def _format_tag_value(self, code: str, value: str, mask_pii: bool = True) -> str:
        """Format tag value based on the code."""

        # Mask PII values if masking is enabled
        if mask_pii and code in self.PII_CODES:
            return self._mask_pii_value(value)

        return value

    def _mask_pii_value(self, value: str) -> str:
        """Apply PII masking to show only last 4 digits for application IDs."""
        if not value or value in ["null", "NA"]:
            return "***"

        # Remove whitespace for processing
        clean_value = value.strip()

        # For application IDs, show only last 4 characters
        if len(clean_value) <= 4:
            return "***"
        else:
            return f"***{clean_value[-4:]}"

    def _format_status_for_display(self, status: str) -> str:
        """Format status with appropriate emoji indicators."""
        return self.format_status_display(status)

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        indent_1 = "  "  # First level indentation
        
        # Scheme name and status from descriptor
        scheme_name = str(self.descriptor)
        if self.descriptor.short_desc:
            status_text = self.descriptor.short_desc.replace("Status: ", "")
            status_formatted = self._format_status_for_display(status_text)
            lines.append(f"> **{scheme_name}**")
            lines.append(f"{indent_1}Status: {status_formatted}")
        else:
            lines.append(f"> **{scheme_name}**")

        # Priority information to display
        priority_info = [
            ("financial_year", "Financial Year"),
            ("application_id", "Application ID"),
            ("last_updated_date", "Last Updated"),
            ("component_updated_date", "Component Updated"),
            ("disbursement_date", "Disbursement Date"),
            ("instalment_status", "Instalment Status"),
        ]

        for code, label in priority_info:
            value = self._get_tag_value(code)
            if value and value != "NA":
                if code == "financial_year":
                    # Format financial year nicely
                    if len(value) == 4:
                        fy_start = f"20{value[:2]}"
                        fy_end = f"20{value[2:]}"
                        lines.append(f"{indent_1}{label}: {fy_start}-{fy_end}")
                    else:
                        lines.append(f"{indent_1}{label}: {value}")
                elif "date" in code and value != "NA":
                    # Format dates nicely
                    try:
                        date_obj = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                        formatted_date = date_obj.strftime("%d %b %Y")
                        lines.append(f"{indent_1}{label}: {formatted_date}")
                    except ValueError:
                        lines.append(f"{indent_1}{label}: {value}")
                else:
                    # Apply PII masking for sensitive codes
                    formatted_value = self._format_tag_value(code, value, mask_pii)
                    lines.append(f"{indent_1}{label}: {formatted_value}")

        return "\n".join(lines)

class Provider(BaseModel):
    id: str
    descriptor: Descriptor
    items: List[SchemeApplication]

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        indent_1 = "  "  # First level indentation
        lines.append(f"Provider: {self.descriptor.name}")

        if self.items:
            lines.append("Applications:")
            for item in self.items:
                item_str = item.__str__(mask_pii=mask_pii).replace("\n", f"\n{indent_1}")
                lines.append(f"{indent_1}{item_str}")

        return "\n".join(lines)

# -----------------------
# Catalog & Message Models
# -----------------------
class Catalog(BaseModel):
    descriptor: Optional[Descriptor] = None
    providers: List[Provider]

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        if self.providers:
            for provider in self.providers:
                provider_str = provider.__str__(mask_pii=mask_pii).replace("\n", "\n")
                lines.append(provider_str)
        return "\n".join(lines)

class Message(BaseModel):
    catalog: Catalog

    def __str__(self, mask_pii: bool = True) -> str:
        return self.catalog.__str__(mask_pii=mask_pii)

# -----------------------
# Context & Response Models
# -----------------------
class Context(BaseModel):
    ttl: Optional[str] = None
    action: str
    timestamp: str
    message_id: str
    transaction_id: str
    domain: str
    version: str
    bap_id: Optional[str] = None
    bap_uri: Optional[AnyHttpUrl] = None
    bpp_id: Optional[str] = None
    bpp_uri: Optional[AnyHttpUrl] = None
    country: Optional[str] = None
    city: Optional[str] = None
    location: Optional[Dict[str, Any]] = None

class ResponseItem(BaseModel):
    context: Context
    message: Message

    def __str__(self, mask_pii: bool = True) -> str:
        return self.message.__str__(mask_pii=mask_pii)

class SchemeStatusResponse(BaseModel):
    context: Context
    responses: List[ResponseItem]

    def _has_scheme_data(self) -> bool:
        """Check if there are any responses with scheme information."""
        for response in self.responses:
            for provider in response.message.catalog.providers:
                if provider.items and len(provider.items) > 0:
                    return True
        return False

    def _get_scheme_summary(self) -> Dict[str, int]:
        """Get a summary of scheme statuses."""
        status_counts = {}
        total_applications = 0

        for response in self.responses:
            for provider in response.message.catalog.providers:
                for item in provider.items:
                    total_applications += 1
                    if item.descriptor.short_desc:
                        status = item.descriptor.short_desc.replace("Status: ", "")
                        status_counts[status] = status_counts.get(status, 0) + 1

        return {"total": total_applications, "statuses": status_counts}

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        lines.append("## Scheme Status Information")
        lines.append("")

        has_scheme_data = self._has_scheme_data()
        if not self.responses or not has_scheme_data:
            lines.append("❌ No scheme application information found for the requested farmer ID.")
            return "\n".join(lines)

        # Get summary
        summary = self._get_scheme_summary()
        indent_1 = "  "  # First level indentation
        if summary["total"] > 0:
            lines.append(f"📊 **Summary: {summary['total']} total applications**")
            for status, count in summary["statuses"].items():
                formatted_status = SchemeApplication.format_status_display(status)
                lines.append(f"{indent_1}• {count} {formatted_status}")
            lines.append("")

        # Show detailed information
        lines.append("### Detailed Information:")
        lines.append("")

        # Group applications by actual application ID (before the dash) and scheme
        applications = {}
        for response in self.responses:
            for provider in response.message.catalog.providers:
                for item in provider.items:
                    # Extract base application ID (before the dash)
                    base_app_id = item.id.split('-')[0] if '-' in item.id else item.id
                    scheme_name = str(item.descriptor)
                    status = item.descriptor.short_desc.replace("Status: ", "") if item.descriptor.short_desc else "Unknown"
                    
                    key = f"{base_app_id}_{scheme_name}"
                    
                    if key not in applications:
                        applications[key] = {
                            "base_item": item,
                            "scheme_name": scheme_name,
                            "base_app_id": base_app_id,
                            "components": []
                        }
                    
                    applications[key]["components"].append({
                        "component_id": item.id,
                        "status": status,
                        "item": item
                    })

        # Display applications with component status breakdown
        indent_1 = "  "  # First level indentation
        indent_2 = "    "  # Second level indentation
        
        for app_data in applications.values():
            base_item = app_data["base_item"]
            scheme_name = app_data["scheme_name"]
            components = app_data["components"]
            
            lines.append(f"> **{scheme_name}**")
            
            # Show base application ID (masked)
            app_id_value = app_data["base_app_id"]
            if mask_pii:
                masked_app_id = base_item._mask_pii_value(app_id_value)
                lines.append(f"{indent_1}Application ID: {masked_app_id}")
            else:
                lines.append(f"{indent_1}Application ID: {app_id_value}")
            
            # Count component statuses
            status_counts = {}
            for component in components:
                status = component["status"]
                status_counts[status] = status_counts.get(status, 0) + 1
            
            # Show component status breakdown
            if len(components) > 1:
                lines.append(f"{indent_1}Total {len(components)} Components")
                for status, count in status_counts.items():
                    status_formatted = base_item._format_status_for_display(status)
                    lines.append(f"{indent_2}• {count} {status_formatted}")
            else:
                # Single component, show status directly
                status_formatted = base_item._format_status_for_display(components[0]["status"])
                lines.append(f"{indent_1}Status: {status_formatted}")
            
            # Show other details from the first component (they should be similar across components)
            first_component = components[0]["item"]
            priority_info = [
                ("financial_year", "Financial Year"),
                ("last_updated_date", "Last Updated"),
                ("disbursement_date", "Disbursement Date")
            ]
            
            for code, label in priority_info:
                value = first_component._get_tag_value(code)
                if value and value != "NA":
                    if code == "financial_year":
                        # Format financial year nicely
                        if len(value) == 4:
                            fy_start = f"20{value[:2]}"
                            fy_end = f"20{value[2:]}"
                            lines.append(f"{indent_1}{label}: {fy_start}-{fy_end}")
                        else:
                            lines.append(f"{indent_1}{label}: {value}")
                    elif "date" in code and value != "NA":
                        # Format dates nicely
                        try:
                            from datetime import datetime
                            date_obj = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                            formatted_date = date_obj.strftime("%d %b %Y")
                            lines.append(f"{indent_1}{label}: {formatted_date}")
                        except ValueError:
                            lines.append(f"{indent_1}{label}: {value}")
                    else:
                        lines.append(f"{indent_1}{label}: {value}")
            
            lines.append("")

        return "\n".join(lines)

# -----------------------
# Request Model
# -----------------------
class SchemeStatusRequest(BaseModel):
    """Request model for the cross-network scheme status API.

    Args:
        farmer_id (str): The farmer ID to fetch scheme status information for
    """

    farmer_id: str = Field(..., description="The farmer ID to fetch scheme status information for")

    def get_payload(self) -> Dict[str, Any]:
        """
        Convert the SchemeStatusRequest object to a dictionary.

        Returns:
            Dict[str, Any]: The dictionary representation of the SchemeStatusRequest object
        """
        now = datetime.now()

        return {
            "context": {
                "domain": "advisory:mh-vistaar",
                "ttl": "PT10S",
                "action": "search",
                "version": "1.1.0",
                "bap_id": os.getenv("BAP_ID"),
                "bap_uri": os.getenv("BAP_URI"),
                # "bpp_id": os.getenv("MAHADBT_BPP_ID"),
                # "bpp_uri": os.getenv("MAHADBT_BPP_URI"),
                "message_id": str(uuid.uuid4()),
                "transaction_id": str(uuid.uuid4()),
                "timestamp": str(int(now.timestamp())),
                "location": {"country": {"name": "India", "code": "IND"}},
            },
            "message": {"intent": {"category": {"descriptor": {"code": "farmer-details-info"}}, "item": {"id": self.farmer_id}}},
        }

@observe(name="tool:get_scheme_status", as_type="tool")
async def get_scheme_status(ctx: RunContext[FarmerContext]) -> str:
    """Fetch MahaDBT scheme application status for the logged-in farmer via cross-network.

    Call only after the system prompt's status-clarification flow — when the farmer clearly
    wants MahaDBT / state scheme status. For vague "DBT status" queries, ask follow-up in
    your reply first; do not call this tool until they choose.

    Returns application status, disbursement information, and scheme details.
    The farmer is identified automatically from the login token (farmer_id or registration number).
    """
    farmer_id = await _resolve_farmer_id_from_context(ctx)
    if not farmer_id:
        return (
            "Farmer ID is not available in the context. "
            "MahaDBT scheme status is only available for logged-in farmers with Agristack registration."
        )

    try:
        payload = SchemeStatusRequest(farmer_id=farmer_id).get_payload()
        endpoint = _bap_action_url("search")
        logger.info("Beckn [mahadbt/search] URL: %s", endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=15.0)

        if response.status_code != 200:
            logger.error("Scheme status API returned status code %s", response.status_code)
            return "Scheme status information service is currently unavailable. Please try again later."

        scheme_response = SchemeStatusResponse.model_validate(response.json())
        return str(scheme_response)

    except httpx.TimeoutException as e:
        logger.error("Scheme status API request timed out: %s", e)
        return "Scheme status request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error("Scheme status API request failed: %s", e)
        return f"Scheme status request failed: {str(e)}"

    except UnexpectedModelBehavior:
        logger.warning("Scheme status request exceeded retry limit")
        return "Sorry, the scheme status information is temporarily unavailable. Please try again later."

    except Exception as e:
        logger.error("Error getting scheme status: %s", e)
        raise ModelRetry(f"Unexpected error in scheme status request. {str(e)}") from e


# -----------------------
# PM-KISAN Installment Status (2-step: /init → /status)
# Uses MH middleware domain advisory:mh-vistaar (not schemes:vistaar)
# -----------------------

def _bap_action_url(action: str) -> str:
    """Resolve BAP URL for init/status/search actions."""
    base = (os.getenv("BAP_BASE_URL") or "").strip().rstrip("/")
    if not base:
        endpoint = (os.getenv("BAP_ENDPOINT") or "").strip().rstrip("/")
        if endpoint.endswith("/search"):
            base = endpoint[: -len("/search")]
        else:
            base = endpoint
    if not base:
        raise ValueError("BAP_BASE_URL or BAP_ENDPOINT is not configured")
    return f"{base}/{action.lstrip('/')}"


def _generate_pmkisan_transaction_id(session_id: str, identifier: str) -> str:
    """Stable transaction id across init and status for the same farmer identifier."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, session_id + identifier))


def _normalize_pmkisan_identifier(reg_no: str = "", phone_number: str = "") -> tuple[str, str]:
    input_val = (reg_no or phone_number).strip().upper().replace(" ", "")
    if not input_val:
        return "", ""
    if re.fullmatch(r"\d{10}", input_val):
        return "", input_val
    return input_val, ""


def _pmkisan_beckn_context(*, transaction_id: str, action: str) -> Dict[str, Any]:
    """MH BAP middleware requires advisory:mh-vistaar and bpp_id/bpp_uri for init/status."""
    now = datetime.now(timezone.utc)
    bpp_id = (os.getenv("BHARAT_VISTAAR_BPP_ID") or "").strip()
    bpp_uri = (os.getenv("BHARAT_VISTAAR_BPP_URI") or "").strip()
    context: Dict[str, Any] = {
        "domain": "advisory:mh-vistaar",
        "action": action,
        "version": "1.1.0",
        "bap_id": os.getenv("BAP_ID"),
        "bap_uri": os.getenv("BAP_URI"),
        "bpp_id": bpp_id,
        "bpp_uri": bpp_uri,
        "transaction_id": transaction_id,
        "message_id": str(uuid.uuid4()),
        "timestamp": str(int(now.timestamp())),
        "ttl": "PT10M",
        "location": {"country": {"code": "IND"}, "city": {"code": "*"}},
    }
    return context


def _beckn_response_ok(response: httpx.Response) -> tuple[bool, str]:
    """Return whether a Beckn BAP response is successful and an error hint if not."""
    if response.status_code not in (200, 202):
        return False, response.text[:300]

    text = response.text.strip()
    if not text:
        return False, "empty response body"

    try:
        data = response.json()
    except json.JSONDecodeError:
        return True, ""

    ack_status = (data.get("message") or {}).get("ack", {}).get("status")
    if ack_status == "NACK":
        error = data.get("error") or {}
        return False, error.get("message") or "request rejected by network (NACK)"

    if data.get("error"):
        error = data["error"]
        return False, error.get("message") or str(error)

    return True, ""


class PMKISANInitRequest(BaseModel):
    """Step 1 — POST /init: send registration number or phone to trigger OTP."""

    transaction_id: str
    registration_number: str
    phone_number: str = ""

    def get_payload(self) -> Dict[str, Any]:
        identifier = self.registration_number or self.phone_number
        return {
            "context": _pmkisan_beckn_context(
                transaction_id=self.transaction_id,
                action="init",
            ),
            "message": {
                "order": {
                    "provider": {"id": ""},
                    "items": [{"id": ""}],
                    "fulfillments": [
                        {
                            "customer": {
                                "person": {
                                    "name": "Customer Name",
                                    "tags": [
                                        {
                                            "display": True,
                                            "descriptor": {
                                                "name": "Registration Details",
                                                "code": "reg-details",
                                            },
                                            "list": [
                                                {
                                                    "descriptor": {
                                                        "name": "Registration Number",
                                                        "code": "reg-number",
                                                    },
                                                    "value": identifier,
                                                    "display": True,
                                                }
                                            ],
                                        }
                                    ],
                                },
                                "contact": {"phone": ""},
                            }
                        }
                    ],
                }
            },
        }


class PMKISANStatusRequest(BaseModel):
    """Step 2 — POST /status: submit the 4-digit SMS OTP to fetch installment status."""

    transaction_id: str
    otp: str
    registration_number: str
    phone_number: str = ""

    def get_payload(self) -> Dict[str, Any]:
        identifier = self.registration_number or self.phone_number
        return {
            "context": _pmkisan_beckn_context(
                transaction_id=self.transaction_id,
                action="status",
            ),
            "message": {
                "order_id": self.otp,
                "registration_number": identifier,
                "phone_number": "",
            },
        }


def _format_pmkisan_init_response(data: Dict[str, Any]) -> str:
    lines: list[str] = []
    for block in data.get("responses") or []:
        order = (block.get("message") or {}).get("order") or {}
        for item in order.get("items") or []:
            for tag in item.get("tags") or []:
                descriptor = tag.get("descriptor") or {}
                if descriptor.get("short_desc"):
                    lines.append(descriptor["short_desc"])
                for tag_item in tag.get("list") or []:
                    value = tag_item.get("value")
                    if value:
                        lines.append(str(value))
    return "\n".join(lines) if lines else "OTP request processed. Please check your mobile for the OTP."


def _format_pmkisan_status_response(data: Dict[str, Any]) -> str:
    lines: list[str] = []
    for block in data.get("responses") or []:
        order = (block.get("message") or {}).get("order") or {}
        if order.get("state"):
            lines.append(f"State: **{order['state']}**")
        provider = order.get("provider") or {}
        provider_name = (provider.get("descriptor") or {}).get("name") or provider.get("id")
        if provider_name:
            lines.append(f"Provider: **{provider_name}**")
        for fulfillment in order.get("fulfillments") or []:
            state = fulfillment.get("state") or {}
            descriptor = state.get("descriptor") or {}
            if descriptor.get("name"):
                lines.append(f"Status: {descriptor['name']}")
            if descriptor.get("short_desc"):
                lines.append(descriptor["short_desc"])
            if descriptor.get("long_desc"):
                lines.append(f"\nDetails:\n\n{descriptor['long_desc']}")
        for tag in order.get("tags") or []:
            descriptor = tag.get("descriptor") or {}
            if descriptor.get("short_desc"):
                lines.append(descriptor["short_desc"])
    return "\n".join(lines) if lines else "No PM-KISAN status data available."


@observe(name="tool:pmkisan_installment_init", as_type="tool")
async def pmkisan_installment_init(
    ctx: RunContext[FarmerContext],
    registration_number: str = "",
    phone_number: str = "",
) -> str:
    """Step 1 of PM-KISAN installment status — send OTP to the farmer's registered mobile.

    Call this first when the farmer asks for PM-KISAN installment/beneficiary status.
    Do NOT call this again after the farmer shares their OTP — use `pmkisan_installment_status` instead.

    Args:
        registration_number: PM-KISAN registration number (11-digit). Leave empty if phone_number is provided.
        phone_number: Farmer's registered 10-digit mobile number. Leave empty if registration_number is provided.

    Returns:
        Confirmation that OTP was sent, or an error message.
    """
    reg_no, phone = _normalize_pmkisan_identifier(registration_number, phone_number)
    if not reg_no and not phone:
        raise ModelRetry(
            "Ask the farmer for their PM-KISAN registration number or registered mobile number."
        )

    try:
        identifier = reg_no or phone
        transaction_id = _generate_pmkisan_transaction_id(ctx.deps.session_id, identifier)
        payload = PMKISANInitRequest(
            transaction_id=transaction_id,
            registration_number=reg_no,
            phone_number=phone,
        ).get_payload()
        endpoint = _bap_action_url("init")
        logger.info("Beckn [pmkisan/init] URL: %s", endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=30.0)

        logger.info("Beckn [pmkisan/init] status: %s", response.status_code)
        ok, err_hint = _beckn_response_ok(response)
        if not ok:
            logger.error("PM-KISAN init API failed: %s", err_hint)
            return "PM-KISAN status service is currently unavailable. Please try again later."

        response_text = response.text.strip()
        if not response_text:
            return "PM-KISAN init returned an empty response. Please try again later."

        try:
            return _format_pmkisan_init_response(response.json())
        except json.JSONDecodeError:
            return response_text

    except httpx.TimeoutException:
        logger.error("PM-KISAN init API timed out")
        return "PM-KISAN request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("PM-KISAN init API request failed: %s", e)
        return f"PM-KISAN request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected error in PM-KISAN init: %s", e)
        raise ModelRetry(f"Unexpected error in PM-KISAN init. {e!s}") from e


@observe(name="tool:pmkisan_installment_status", as_type="tool")
async def pmkisan_installment_status(
    ctx: RunContext[FarmerContext],
    otp: str,
    registration_number: str = "",
    phone_number: str = "",
) -> str:
    """Step 2 of PM-KISAN installment status — verify OTP and fetch live installment details.

    Call this after `pmkisan_installment_init` once the farmer provides the 4-digit OTP received via SMS.
    Never call `pmkisan_installment_init` when the farmer shares an OTP.

    Args:
        otp: 4-digit OTP received via SMS on the farmer's registered mobile
        registration_number: Same PM-KISAN registration number used in init. Leave empty if phone_number was used.
        phone_number: Same registered mobile number used in init. Leave empty if registration_number was used.

    Returns:
        PM-KISAN installment status details or an error message.
    """
    reg_no, phone = _normalize_pmkisan_identifier(registration_number, phone_number)
    if not reg_no and not phone:
        raise ModelRetry(
            "Registration number or phone number is required. Ask the farmer for the same identifier used in step 1."
        )

    otp_clean = str(otp).strip()
    if not otp_clean.isdigit() or len(otp_clean) != 4:
        raise ModelRetry("Invalid OTP format. Please provide the 4-digit OTP received via SMS.")

    try:
        identifier = reg_no or phone
        transaction_id = _generate_pmkisan_transaction_id(ctx.deps.session_id, identifier)
        payload = PMKISANStatusRequest(
            transaction_id=transaction_id,
            otp=otp_clean,
            registration_number=reg_no,
            phone_number=phone,
        ).get_payload()
        endpoint = _bap_action_url("status")
        logger.info("Beckn [pmkisan/status] URL: %s", endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=30.0)

        logger.info("Beckn [pmkisan/status] status: %s", response.status_code)
        ok, err_hint = _beckn_response_ok(response)
        if not ok:
            logger.error("PM-KISAN status API failed: %s", err_hint)
            return "PM-KISAN status service is currently unavailable. Please try again later."

        response_text = response.text.strip()
        if not response_text:
            return "PM-KISAN status returned an empty response. Please try again later."

        try:
            return _format_pmkisan_status_response(response.json())
        except json.JSONDecodeError:
            return response_text

    except httpx.TimeoutException:
        logger.error("PM-KISAN status API timed out")
        return "PM-KISAN request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("PM-KISAN status API request failed: %s", e)
        return f"PM-KISAN request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected error in PM-KISAN status: %s", e)
        raise ModelRetry(f"Unexpected error in PM-KISAN status. {e!s}") from e


# -----------------------
# SMAM Application Status (single search step)
# -----------------------

class SMAMStatusRequest(BaseModel):
    """Search request for SMAM application status by application number."""

    application_number: str
    search_type: str = "application_no"

    def get_payload(self) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "context": {
                "domain": "advisory:mh-vistaar",
                "action": "search",
                "version": "1.1.0",
                "bap_id": os.getenv("BAP_ID"),
                "bap_uri": os.getenv("BAP_URI"),
                "message_id": str(uuid.uuid4()),
                "transaction_id": str(uuid.uuid4()),
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "location": {"country": {"name": "India", "code": "IND"}},
            },
            "message": {
                "intent": {
                    "provider": {
                        "id": "smam",
                        "descriptor": {"code": "smam"},
                    },
                    "item": {
                        "descriptor": {"code": "application_status"},
                        "tags": [
                            {
                                "descriptor": {"code": "search_params"},
                                "list": [
                                    {
                                        "descriptor": {"code": "search_type"},
                                        "value": self.search_type,
                                    },
                                    {
                                        "descriptor": {"code": "search_value"},
                                        "value": self.application_number,
                                    },
                                ],
                            }
                        ],
                    },
                }
            },
        }


@observe(name="tool:smam_application_status", as_type="tool")
async def smam_application_status(application_number: str) -> str:
    """Fetch SMAM (Sub Mission on Agriculture Mechanization) application status by application number.

    Use this when the farmer asks for the status of their SMAM application.

    Args:
        application_number: SMAM application number (e.g. UK000082623/2025-26/1)

    Returns:
        SMAM application status details or an error message.
    """
    application_number = (application_number or "").strip()
    if not application_number:
        raise ModelRetry("Ask the farmer for their SMAM application number before calling this tool.")

    try:
        payload = SMAMStatusRequest(application_number=application_number).get_payload()
        endpoint = _bap_action_url("search")
        logger.info("Beckn [smam/search] URL: %s", endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=30.0)

        if response.status_code != 200:
            logger.error("SMAM status API returned %s: %s", response.status_code, response.text[:300])
            return "SMAM status service is currently unavailable. Please try again later."

        return response.text.strip() or "SMAM status returned an empty response."

    except httpx.TimeoutException:
        logger.error("SMAM status API timed out")
        return "SMAM request timed out. Please try again later."
    except httpx.RequestError as e:
        logger.error("SMAM status API request failed: %s", e)
        return f"SMAM request failed: {e!s}"
    except Exception as e:
        logger.error("Unexpected error in SMAM status: %s", e)
        raise ModelRetry(f"Unexpected error in SMAM status. {e!s}") from e


# -----------------------
# POCRA DBT Application Status (logged-in farmer via cross-network)
# -----------------------

def _extract_farmer_id_from_agristack_payload(data: Dict[str, Any]) -> Optional[str]:
    """Parse farmer_id from an agristack_farmer_info Beckn response."""
    for block in data.get("responses") or []:
        catalog = (block.get("message") or {}).get("catalog") or {}
        for provider in catalog.get("providers") or []:
            for item in provider.get("items") or []:
                item_id = str(item.get("id") or "")
                if item_id.startswith("farmer-"):
                    return item_id.removeprefix("farmer-")
                if item_id.isdigit():
                    return item_id
                for tag in item.get("tags") or []:
                    code = tag.get("code") or (tag.get("descriptor") or {}).get("code")
                    if code in {"farmer_id", "agristack_farmerid"} and tag.get("value"):
                        return str(tag["value"])
    return None


async def _lookup_farmer_id_via_agristack(registration_number: str) -> Optional[str]:
    """Resolve Agristack farmer_id from a registration number via cross-network search."""
    now = datetime.now()
    payload = {
        "context": {
            "domain": "advisory:mh-vistaar",
            "action": "search",
            "version": "1.1.0",
            "bap_id": os.getenv("BAP_ID"),
            "bap_uri": os.getenv("BAP_URI"),
            "bpp_id": os.getenv("POCRA_BPP_ID"),
            "bpp_uri": os.getenv("POCRA_BPP_URI"),
            "location": {"country": {"name": "India", "code": "IND"}},
            "transaction_id": str(uuid.uuid4()),
            "message_id": str(uuid.uuid4()),
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        },
        "message": {
            "intent": {
                "category": {"descriptor": {"code": "agristack_farmer_info"}},
                "item": {"id": registration_number},
            }
        },
    }
    endpoint = _bap_action_url("search")
    logger.info("Beckn [agristack/lookup] URL: %s", endpoint)

    async with httpx.AsyncClient() as client:
        response = await client.post(endpoint, json=payload, timeout=15.0)

    if response.status_code != 200:
        logger.error("Agristack lookup returned %s: %s", response.status_code, response.text[:300])
        return None

    try:
        return _extract_farmer_id_from_agristack_payload(response.json())
    except json.JSONDecodeError:
        logger.error("Agristack lookup returned non-JSON body")
        return None


async def _resolve_farmer_id_from_context(ctx: RunContext[FarmerContext]) -> Optional[str]:
    """Use JWT farmer_id, or resolve from registration number (unique_id) via Agristack."""
    if ctx.deps.farmer_id:
        return str(ctx.deps.farmer_id).strip()

    registration_number = (ctx.deps.unique_id or "").strip()
    if not registration_number:
        return None

    farmer_id = await _lookup_farmer_id_via_agristack(registration_number)
    if farmer_id:
        ctx.deps.update_farmer_id(farmer_id)
        logger.info("Resolved Agristack farmer ID from registration number")
    return farmer_id


@observe(name="tool:get_pocra_dbt_status", as_type="tool")
async def get_pocra_dbt_status(
    ctx: RunContext[FarmerContext],
    application_id: Optional[str] = None,
) -> str:
    """Fetch POCRA DBT application status for the logged-in farmer via cross-network.

    Call only after the system prompt's status-clarification flow — when the farmer clearly
    wants POCRA DBT status. For vague queries or first-time POCRA DBT requests, ask
    follow-up in your reply first (all applications vs one specific number); do not call
    this tool until they answer.

    The farmer is identified automatically from the login token (farmer_id or registration number).
    Each farmer may have multiple DBT applications.

    Args:
        application_id: Full POCRA DBT application number for a single-application lookup.
            Omit when the farmer wants all applications.

    Returns:
        POCRA DBT application status summary or details.
    """
    from agents.tools.pocra_dbt import PocraDBTRequest, PocraDBTResponse

    farmer_id = await _resolve_farmer_id_from_context(ctx)
    if not farmer_id:
        return (
            "Farmer ID is not available in the context. "
            "POCRA DBT status is only available for logged-in farmers with Agristack registration."
        )

    if application_id is not None:
        resolved_application_id = str(application_id).strip() or None
    else:
        resolved_application_id = None

    try:
        logger.info("POCRA DBT: calling network API")
        payload = PocraDBTRequest(
            farmer_id=farmer_id,
            application_id=resolved_application_id,
        ).get_payload()
        endpoint = _bap_action_url("search")
        logger.info("Beckn [pocra-dbt/search] URL: %s", endpoint)

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, json=payload, timeout=15.0)

        if response.status_code != 200:
            logger.error("POCRA DBT API returned status code %s", response.status_code)
            return "POCRA DBT application status service is currently unavailable. Please try again later."

        dbt_response = PocraDBTResponse.model_validate(response.json())
        return dbt_response.format_status(application_id=resolved_application_id)

    except httpx.TimeoutException as e:
        logger.error("POCRA DBT API request timed out: %s", e)
        return "POCRA DBT application status request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error("POCRA DBT API request failed: %s", e)
        return f"POCRA DBT application status request failed: {str(e)}"

    except UnexpectedModelBehavior:
        logger.warning("POCRA DBT request exceeded retry limit")
        return "Sorry, POCRA DBT application status information is temporarily unavailable. Please try again later."

    except Exception as e:
        logger.error("Error getting POCRA DBT application status: %s", e)
        raise ModelRetry(f"Unexpected error in POCRA DBT application status request. {str(e)}") from e
