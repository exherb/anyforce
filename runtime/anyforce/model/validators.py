from typing import Any

from email_validator import EmailNotValidError, validate_email


def email(value: Any):
    try:
        validate_email(str(value), check_deliverability=False)
    except EmailNotValidError:
        raise ValueError(f"{value} 不是有效的邮箱地址")
