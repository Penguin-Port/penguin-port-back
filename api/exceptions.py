import uuid

from django.core.exceptions import (
    ObjectDoesNotExist,
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def problem_response(*, request, detail: str, code: str, status: int, retryable=False):
    request_id = request.headers.get("X-Request-Id", f"req_{uuid.uuid4().hex}")
    return Response(
        {
            "type": f"https://api.smart-wifi-pass.local/problems/{code.lower().replace('_', '-')}",
            "title": code.replace("_", " ").title(),
            "status": status,
            "code": code,
            "detail": detail,
            "retryable": retryable,
            "requestId": request_id,
        },
        status=status,
    )


def problem_exception_handler(exc, context):
    response = exception_handler(exc, context)
    request = context["request"]
    if response is None:
        if isinstance(exc, ObjectDoesNotExist):
            return problem_response(
                request=request,
                detail="요청한 리소스를 찾을 수 없습니다.",
                code="RESOURCE_NOT_FOUND",
                status=404,
            )
        if isinstance(exc, DjangoPermissionDenied):
            return problem_response(
                request=request,
                detail=str(exc),
                code="PERMISSION_DENIED",
                status=403,
            )
        if isinstance(exc, DjangoValidationError):
            return problem_response(
                request=request,
                detail=str(exc),
                code="VALIDATION_ERROR",
                status=422,
            )
        if isinstance(exc, IntegrityError):
            return problem_response(
                request=request,
                detail="중복되거나 참조 무결성을 위반한 요청입니다.",
                code="INTEGRITY_CONFLICT",
                status=409,
            )
        return None
    detail = response.data.get("detail", response.data)
    return problem_response(
        request=request,
        detail=str(detail),
        code=getattr(exc, "default_code", "REQUEST_FAILED").upper(),
        status=response.status_code,
    )
