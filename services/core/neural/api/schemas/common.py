from typing import Any, Optional

from pydantic import BaseModel


class StatusResponse(BaseModel):
    status: str
    message: str
    data: Optional[dict[str, Any]] = None


class PaginationParams(BaseModel):
    limit: int = 50
    offset: int = 0

