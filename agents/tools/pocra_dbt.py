"""

POCRA DBT application status

"""

import os
import uuid
from datetime import datetime
from helpers.utils import get_logger
from pydantic import BaseModel, AnyHttpUrl, Field
from typing import List, Optional, Dict, Any, ClassVar
from dotenv import load_dotenv

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
        "InMeeting": "In Meeting",
        "Approved": "Approved",
        "Rejected": "Rejected",
        "Pending": "Pending",
        "Fund Disbursed": "Fund Disbursed",
        "Cancelled": "Cancelled",
    }

    STAGE_LABELS: ClassVar[Dict[str, str]] = {
        "GKVS": "Village Level Committee (GKVS)",
        "TKVS": "Taluka Level Committee (TKVS)",
        "DKVS": "District Level Committee (DKVS)",
    }

    @classmethod
    def format_status_display(cls, status: str) -> str:
        return cls.STATUS_LABELS.get(status, status)

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

    def _activity_name(self) -> str:
        return self._get_tag_value("activity_name") or str(self.descriptor).split(" - ")[0]

    def _title(self) -> str:
        """Title as activity_name (unit_name), matching the POCRA DBT portal."""
        activity_name = self._activity_name()
        unit_name = self._get_tag_value("unit_name")
        if unit_name:
            return f"{activity_name} ({unit_name})"
        return activity_name

    def _village_display(self) -> Optional[str]:
        village_name = self._get_tag_value("village_name")
        village_code = self._get_tag_value("village_code")
        if village_name and village_code:
            return f"{village_name} ({village_code})"
        return village_name or village_code

    def _unit_size_display(self) -> Optional[str]:
        unit_size = self._get_tag_value("unit_size")
        unit_size_type = self._get_tag_value("unit_size_type")
        if unit_size and unit_size_type:
            return f"{unit_size} {unit_size_type}"
        return unit_size or unit_size_type

    def _format_amount(self, value: str) -> str:
        clean = value.strip()
        if not clean or clean in {"null", "NA"}:
            return clean
        try:
            amount = float(clean.replace(",", ""))
            if amount.is_integer():
                return f"₹{int(amount):,}"
            return f"₹{amount:,.2f}"
        except ValueError:
            return clean

    def _status_content_bullets(self, mask_pii: bool = True) -> list[str]:
        """Shared content bullets for list and single-application views."""
        app_id = self._get_tag_value("application_id") or self.id
        masked_app_id = self._format_tag_value("application_id", app_id, mask_pii)
        status = self._get_tag_value("application_status")
        unit_size = self._unit_size_display()
        village = self._village_display()
        stage = self._get_tag_value("application_stage")
        app_date = self._get_tag_value("application_date")
        amount = self._get_tag_value("presanctionamount")
        survey_no = self._get_tag_value("survey_no")

        bullets: list[str] = [f"- Application ID: {masked_app_id}"]
        if status:
            bullets.append(f"- Status: {self.format_status_display(status)}")
        if unit_size:
            bullets.append(f"- Unit size: {unit_size}")
        if village:
            bullets.append(f"- Village: {village}")
        if stage:
            bullets.append(f"- Stage: {self.format_stage_display(stage)}")
        if app_date:
            bullets.append(f"- Applied on: {self._format_date(app_date)}")
        if amount:
            bullets.append(f"- Pre-sanction amount: {self._format_amount(amount)}")
        if survey_no:
            bullets.append(f"- Survey No: {survey_no}")
        return bullets

    def to_summary_card(self, index: int, mask_pii: bool = True) -> str:
        """Numbered card with GFM sub-bullets for the all-applications list view."""
        lines = [f"**{index}. {self._title()}**"]
        lines.extend(self._status_content_bullets(mask_pii=mask_pii))
        return "\n".join(lines)

    def matches_application_id(self, application_id: str) -> bool:
        normalized = application_id.strip()
        if self.id == normalized:
            return True
        tag_app_id = self._get_tag_value("application_id")
        return tag_app_id == normalized

    def to_detail_block(self, mask_pii: bool = True) -> str:
        """Structured detail block for a single application (same fields as list)."""
        lines = [f"**{self._title()}**", ""]
        lines.extend(self._status_content_bullets(mask_pii=mask_pii))
        return "\n".join(lines)

    def __str__(self, mask_pii: bool = True) -> str:
        return self.to_detail_block(mask_pii=mask_pii)


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
        applications = self._collect_applications()

        if application_id:
            applications = [
                item for item in applications if item.matches_application_id(application_id)
            ]
            if not applications:
                return (
                    f"No POCRA DBT application found with application number "
                    f"{application_id} for this farmer."
                )

            lines: list[str] = []
            for idx, item in enumerate(applications, start=1):
                if len(applications) > 1:
                    lines.append(f"**Application {idx}**")
                    lines.append("")
                lines.append(item.to_detail_block(mask_pii=mask_pii))
                if idx < len(applications):
                    lines.append("")
                    lines.append("---")
                    lines.append("")
            return "\n".join(lines).rstrip()

        if not applications:
            return "No POCRA DBT application information found for this farmer."

        count = len(applications)
        noun = "application" if count == 1 else "applications"
        lines = [
            f"You have **{count}** POCRA DBT {noun}.",
            "",
        ]

        for idx, item in enumerate(applications, start=1):
            lines.append(item.to_summary_card(idx, mask_pii=mask_pii))
            if idx < count:
                lines.append("")

        return "\n".join(lines).rstrip()

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



