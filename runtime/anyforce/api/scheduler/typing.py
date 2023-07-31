from datetime import datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
)

from pydantic import BaseModel, Field

from ...model.enum import StrEnum


class JobStatus(StrEnum):
    pending = "pending", "等待"
    failed = "failed", "失败"
    finished = "finished", "完成"
    canceled = "canceled", "取消"


class Job(BaseModel):
    id: str
    at: datetime
    func: Optional[Callable[..., Any]]
    status: JobStatus = JobStatus.pending
    args: List[Any] = Field(default_factory=list)
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    explain_func: str = ""
    explain_lines: List[Tuple[str, List[Tuple[str, Any]]]] = Field(default_factory=list)
    exc_info: Optional[str] = None
    return_value: Optional[Any] = None


class Response(BaseModel):
    total: int
    data: List[Job]


class Worker(Protocol):
    def enqueue_at(
        self, datetime: datetime, f: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        raise NotImplementedError()

    def list(
        self, offset: int, limit: int, condition: Optional[Dict[str, str]]
    ) -> Awaitable[Response]:
        raise NotImplementedError()

    def cancel(self, id: str) -> Any:
        raise NotImplementedError()
