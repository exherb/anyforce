class EmailNotValidError(Exception): ...

def validate_email(email: str, check_deliverability: bool = ...) -> None: ...
