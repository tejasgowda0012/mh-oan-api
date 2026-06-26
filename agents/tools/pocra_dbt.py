"""

POCRA DBT application status

"""

import os
import uuid
from datetime import datetime
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
        return "Unknown Application"


class TagListItem(BaseModel):
    descriptor: Optional[Descriptor] = None
    value: str
    display: Optional[bool] = None


class TagGroup(BaseModel):
    display: Optional[bool] = None
    descriptor: Optional[Descriptor] = None
    list: List[TagListItem] = Field(default_factory=list)


# -----------------------
# Application Models
# -----------------------
class DBTApplication(BaseModel):
    """Model representing a POCRA DBT application with status and details."""

    id: str
    descriptor: Descriptor
    tags: List[TagGroup]

    PII_CODES: ClassVar[set[str]] = {
        "application_id",
        "agristack_farmerid",
        "farm_id",
    }

    STATUS_LABELS: ClassVar[Dict[str, str]] = {
        "InMeeting": "📋 In Meeting (समिती बैठकीत आहे)",
        "Approved": "✅ Approved (मंजूर)",
        "Rejected": "❌ Rejected (नाकारले)",
        "Pending": "⏳ Pending (प्रलंबित)",
        "Fund Disbursed": "✅ Fund Disbursed (निधी वितरित)",
        "Cancelled": "🚫 Cancelled (रद्द)",
    }

    STAGE_LABELS: ClassVar[Dict[str, str]] = {
        "GKVS": "GKVS (ग्राम कृषी विकास समिती)",
        "TKVS": "TKVS (तालुका कृषी विकास समिती)",
        "DKVS": "DKVS (जिल्हा कृषी विकास समिती)",
    }

    @classmethod
    def format_status_display(cls, status: str) -> str:
        return cls.STATUS_LABELS.get(status, f"📄 {status}")

    @classmethod
    def format_stage_display(cls, stage: str) -> str:
        return cls.STAGE_LABELS.get(stage, stage)

    def _get_tag_value(self, code: str) -> Optional[str]:
        for tag_group in self.tags:
            for item in tag_group.list:
                if item.descriptor and item.descriptor.code == code:
                    if item.value in ["null", "NA", ""]:
                        return None
                    return item.value
        return None

    def _mask_pii_value(self, value: str) -> str:
        if not value or value in ["null", "NA"]:
            return "***"

        clean_value = value.strip()
        if len(clean_value) <= 4:
            return "***"
        return f"***{clean_value[-4:]}"

    def _format_tag_value(self, code: str, value: str, mask_pii: bool = True) -> str:
        if mask_pii and code in self.PII_CODES:
            return self._mask_pii_value(value)
        return value

    def _format_date(self, value: str) -> str:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, fmt).strftime("%d %b %Y")
            except ValueError:
                continue
        return value

    def to_summary_line(self, mask_pii: bool = True) -> str:
        activity_name = self._get_tag_value("activity_name") or str(self.descriptor).split(" - ")[0]
        status = self._get_tag_value("application_status")
        stage = self._get_tag_value("application_stage")
        village = self._get_tag_value("village_name")
        app_id = self._get_tag_value("application_id") or self.id
        masked_app_id = self._format_tag_value("application_id", app_id, mask_pii)

        parts = [f"**{activity_name}**", f"Application ID: {masked_app_id}"]
        if status:
            parts.append(f"Status: {self.format_status_display(status)}")
        if stage:
            parts.append(f"Stage: {self.format_stage_display(stage)}")
        if village:
            parts.append(f"Village: {village}")
        return " | ".join(parts)

    def matches_application_id(self, application_id: str) -> bool:
        normalized = application_id.strip()
        if self.id == normalized:
            return True
        tag_app_id = self._get_tag_value("application_id")
        return tag_app_id == normalized

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        indent_1 = "  "

        activity_name = self._get_tag_value("activity_name") or str(self.descriptor).split(" - ")[0]
        status = self._get_tag_value("application_status")
        stage = self._get_tag_value("application_stage")

        lines.append(f"> **{activity_name}**")
        if status:
            lines.append(f"{indent_1}Status: {self.format_status_display(status)}")
        if stage:
            lines.append(f"{indent_1}Stage: {self.format_stage_display(stage)}")

        priority_info = [
            ("full_name", "Applicant Name"),
            ("application_id", "Application ID"),
            ("application_date", "Application Date"),
            ("village_name", "Village"),
            ("survey_no", "Survey No"),
            ("activity_group_name", "Activity Group"),
            ("unit_name", "Unit"),
            ("area_applied", "Area Applied (ha)"),
            ("area_as_per_farmer_id", "Area As Per Farmer ID (ha)"),
            ("presanctionamount", "Pre-sanction Amount"),
            ("remark", "Remark"),
            ("reasons_text", "Reason"),
            ("last_updated_on", "Last Updated"),
            ("is_maha_dbt_application", "MahaDBT Application"),
        ]

        for code, label in priority_info:
            value = self._get_tag_value(code)
            if value is None:
                continue
            if code.endswith(("_date", "_on")):
                value = self._format_date(value)
            else:
                value = self._format_tag_value(code, value, mask_pii)
            lines.append(f"{indent_1}{label}: {value}")

        return "\n".join(lines)


class Provider(BaseModel):
    id: str
    descriptor: Descriptor
    items: List[DBTApplication]

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        indent_1 = "  "
        lines.append(f"Provider: {self.descriptor.name}")

        if self.items:
            lines.append("Applications:")
            for item in self.items:
                item_str = item.__str__(mask_pii=mask_pii).replace("\n", f"\n{indent_1}")
                lines.append(f"{indent_1}{item_str}")

        return "\n".join(lines)


class Catalog(BaseModel):
    descriptor: Optional[Descriptor] = None
    providers: List[Provider]

    def __str__(self, mask_pii: bool = True) -> str:
        lines = []
        for provider in self.providers:
            lines.append(provider.__str__(mask_pii=mask_pii))
        return "\n".join(lines)


class Message(BaseModel):
    catalog: Catalog

    def __str__(self, mask_pii: bool = True) -> str:
        return self.catalog.__str__(mask_pii=mask_pii)


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


class PocraDBTResponse(BaseModel):
    context: Context
    responses: List[ResponseItem]

    def _collect_applications(self) -> List[DBTApplication]:
        seen_ids: set[str] = set()
        applications: List[DBTApplication] = []

        for response in self.responses:
            for provider in response.message.catalog.providers:
                for item in provider.items:
                    if item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    applications.append(item)

        return applications

    def format_status(
        self,
        mask_pii: bool = True,
        application_id: Optional[str] = None,
    ) -> str:
        lines = ["## POCRA DBT Application Status", ""]
        applications = self._collect_applications()

        if application_id:
            applications = [
                item for item in applications if item.matches_application_id(application_id)
            ]
            if not applications:
                lines.append(
                    f"❌ No POCRA DBT application found with application number {application_id} for this farmer ID."
                )
                return "\n".join(lines)

            lines.append("### Application Details:")
            lines.append("")
            for item in applications:
                lines.append(item.__str__(mask_pii=mask_pii))
                lines.append("")
            return "\n".join(lines).rstrip()

        if not applications:
            lines.append("❌ No POCRA DBT application information found for the requested farmer ID.")
            return "\n".join(lines)

        latest_response = self.responses[-1] if self.responses else None
        if latest_response and latest_response.message.catalog.descriptor:
            descriptor = latest_response.message.catalog.descriptor
            if descriptor.short_desc:
                lines.append(descriptor.short_desc)
                lines.append("")

        lines.append(f"📊 **Summary: {len(applications)} total applications**")
        lines.append("")
        for item in applications:
            lines.append(f"- {item.to_summary_line(mask_pii=mask_pii)}")
        return "\n".join(lines)

    def __str__(self, mask_pii: bool = True) -> str:
        return self.format_status(mask_pii=mask_pii)


class PocraDBTRequest(BaseModel):
    """POCRA DBT Request model for the POCRA DBT status API."""

    farmer_id: str = Field(..., description="The Agristack farmer ID")
    application_id: Optional[str] = Field(
        default=None,
        description="Optional POCRA DBT application ID for a specific application lookup",
    )

    def get_payload(self) -> Dict[str, Any]:
        now = datetime.now()
        item: Dict[str, Any] = {"id": self.farmer_id}
        if self.application_id:
            item["descriptor"] = {"code": self.application_id}

        return {
            "context": {
                "domain": "advisory:mh-vistaar",
                "ttl": "PT10S",
                "action": "search",
                "version": "1.1.0",
                "bap_id": os.getenv("BAP_ID"),
                "bap_uri": os.getenv("BAP_URI"),
                "bpp_id": os.getenv("POCRA_BPP_ID"),
                "bpp_uri": os.getenv("POCRA_BPP_URI"),
                "message_id": str(uuid.uuid4()),
                "transaction_id": str(uuid.uuid4()),
                "timestamp": str(int(now.timestamp())),
                "location": {"country": {"code": "IND"}},
            },
            "message": {
                "intent": {
                    "category": {"descriptor": {"code": "pocra-dbt-status"}},
                    "item": item,
                }
            },
        }


@observe(name="tool:get_pocra_dbt_status", as_type="tool")
async def get_pocra_dbt_status(
    ctx: RunContext[FarmerContext],
    application_id: Optional[str] = None,
) -> str:
    """Fetch POCRA DBT application status for the logged-in farmer.

    Returns a summary of the farmer's POCRA DBT applications and their status.
    Pass application_id to get details for one application; omit it to list all applications.
    """
    if ctx.deps.farmer_id:
        farmer_id = ctx.deps.farmer_id
    else:
        return "Farmer ID is not available in the context. Please register with your farmer ID."

    if application_id is not None:
        resolved_application_id = str(application_id).strip() or None
    else:
        resolved_application_id = None

    try:
        payload = PocraDBTRequest(
            farmer_id=farmer_id,
            application_id=resolved_application_id,
        ).get_payload()

        async with httpx.AsyncClient() as client:
            response = await client.post(os.getenv("BAP_ENDPOINT"), json=payload, timeout=15.0)

        if response.status_code != 200:
            logger.error(f"POCRA DBT API returned status code {response.status_code}")
            return "POCRA DBT application status service is currently unavailable. Please try again later."

        dbt_response = PocraDBTResponse.model_validate(response.json())
        return dbt_response.format_status(application_id=resolved_application_id)

    except httpx.TimeoutException as e:
        logger.error(f"POCRA DBT API request timed out: {str(e)}")
        return "POCRA DBT application status request timed out. Please try again later."

    except httpx.RequestError as e:
        logger.error(f"POCRA DBT API request failed: {e}")
        return f"POCRA DBT application status request failed: {str(e)}"

    except UnexpectedModelBehavior:
        logger.warning("POCRA DBT request exceeded retry limit")
        return "Sorry, POCRA DBT application status information is temporarily unavailable. Please try again later."

    except Exception as e:
        logger.error(f"Error getting POCRA DBT application status: {e}")
        raise ModelRetry(f"Unexpected error in POCRA DBT application status request. {str(e)}")
