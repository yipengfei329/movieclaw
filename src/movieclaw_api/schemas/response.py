from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    code: str = "OK"
    message: str = "success"
    data: T


class ErrorResponse(BaseModel):
    success: bool = False
    code: str
    message: str
    details: Optional[list[dict[str, Any]]] = Field(default=None)


def ok(data: T, message: str = "success", code: str = "OK") -> ApiResponse[T]:
    return ApiResponse[T](success=True, code=code, message=message, data=data)
