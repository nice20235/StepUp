def hash_password(password: str) -> str:
    """Return password as is (no hashing)"""
    return password


def verify_password(password: str, stored_password: str) -> bool:
    """Verify password by direct comparison"""
    return password == stored_password 