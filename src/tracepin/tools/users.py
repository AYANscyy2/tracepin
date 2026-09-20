"""Two near-identical user lookups with incompatible signatures.

The confusable pair is deliberate: it produces wrong-tool selection and argument-type
errors without any fabricated bug.
"""

from pydantic import BaseModel

from tracepin.instrument import traced_tool

_USERS_BY_ID = {
    101: {"user_id": 101, "username": "alice", "email": "alice@example.com", "plan": "pro", "country": "US"},
    102: {"user_id": 102, "username": "bob", "email": "bob@example.com", "plan": "free", "country": "CA"},
    103: {"user_id": 103, "username": "carol", "email": "carol@example.com", "plan": "enterprise", "country": "UK"},
    104: {"user_id": 104, "username": "dave", "email": "dave@example.com", "plan": "pro", "country": "DE"},
    105: {"user_id": 105, "username": "erin", "email": "erin@example.com", "plan": "free", "country": "IN"},
}
_USERS_BY_NAME = {u["username"]: u for u in _USERS_BY_ID.values()}


class GetUserArgs(BaseModel):
    user_id: int


class FetchUserArgs(BaseModel):
    username: str


@traced_tool(description="Look up a user by their numeric user_id.")
def get_user(user_id: int) -> dict:
    user = _USERS_BY_ID.get(user_id)
    if user is None:
        raise KeyError(f"no user with user_id={user_id!r}")
    return user


@traced_tool(description="Retrieve the profile record for a username string.")
def fetch_user(username: str) -> dict:
    user = _USERS_BY_NAME.get(username)
    if user is None:
        raise KeyError(f"no user with username={username!r}")
    return user
