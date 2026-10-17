#
# Tool arguments: one pydantic model per catalog tool.
#
# The model is both the JSON schema the LLM sees (see tools.schema_properties) and the
# parser its arguments go through before a tool runs. Unknown keys are ignored; ids and
# the catalog's finite vocabularies (specialty, language) are checked
# against the database in tools._validate, since they change per catalog.
#

from typing import Annotated, Literal, Optional

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field

# a weekday in any case: "saturday" reads as "Saturday"
Day = Annotated[
    Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    BeforeValidator(lambda v: v.strip().title() if isinstance(v, str) else v),
]


class LookupPatientArgs(BaseModel):
    full_name: str
    date_of_birth: str = Field(description="YYYY-MM-DD")


class SearchAppointmentTypesArgs(BaseModel):
    query: str = Field(
        description="The caller's own words, e.g. 'skin check' or 'my knee hurts'."
    )
    specialty: Optional[str] = Field(None, description="Optional specialty filter.")
    provider_id: Optional[str] = Field(
        None, description="Only types this provider can do."
    )
    location_id: Optional[str] = Field(
        None, description="Only types bookable at this location."
    )


class FindProvidersArgs(BaseModel):
    name: Optional[str] = Field(
        None, description="As the caller said it, e.g. 'Dr. Nguyen'."
    )
    specialty: Optional[str] = None
    appointment_type_id: Optional[str] = Field(
        None, description="Only providers who can do this type."
    )
    location_id: Optional[str] = None
    language: Optional[str] = None
    accepting_new_patients: Optional[bool] = None


class FindLocationsArgs(BaseModel):
    name: Optional[str] = None
    near: Optional[str] = Field(
        None, description="Place the caller named, e.g. 'Sunset' or 'Geary Blvd'."
    )
    open_on: Optional[Day] = None
    appointment_type_id: Optional[str] = Field(
        None, description="Only locations where this type is bookable."
    )
    provider_id: Optional[str] = Field(
        None, description="Only locations where this provider works."
    )


class CheckAvailabilityArgs(BaseModel):
    appointment_type_id: str
    provider_id: Optional[str] = None
    location_id: Optional[str] = None
    near: Optional[str] = Field(
        None, description="A place the caller named, to rank by distance."
    )
    on_or_after: Optional[str] = Field(
        None, description="YYYY-MM-DD, when the caller can't come sooner."
    )
    part_of_day: Optional[Literal["any", "morning", "afternoon"]] = None


class ListSlotsArgs(BaseModel):
    provider_id: str
    location_id: str
    appointment_type_id: str


class BookAppointmentArgs(BaseModel):
    appointment_type_id: str
    provider_id: str
    location_id: str
    start: str = Field(
        validation_alias=AliasChoices("start", "slot"),
        description="The slot's 'start' value, e.g. 2026-10-13T09:30.",
    )
