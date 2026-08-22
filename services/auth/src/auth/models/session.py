from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    expires_at: datetime
    created_at: datetime
