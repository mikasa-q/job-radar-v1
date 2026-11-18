"""Pydantic response contracts, independent of SQLite implementation details."""
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class JobResponse(BaseModel):
    id: int
    source: str
    title: str
    company: str = ""
    city: str = ""
    url: str
    description: str = ""
    description_kind: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    skills: list[str] = Field(default_factory=list)
    posted_at: str = ""
    crawled_at: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""


class JobsPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobResponse]


class GroupCount(BaseModel):
    value: str
    count: int


class JobsStats(BaseModel):
    total: int
    by_city: list[GroupCount]
    by_source: list[GroupCount]
