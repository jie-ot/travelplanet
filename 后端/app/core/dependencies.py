"""Global FastAPI dependencies.

Authentication / user isolation master definition: 《后端技术栈与全局规范》四.
The demo is single-user and login-less; routes must never read the user
constant from config directly — they must inject `current_user_id` via
`Depends(get_current_user_id)`. Upgrading to JWT later only changes this
function; business code stays untouched.
"""

from __future__ import annotations

from app.core.config import settings


def get_current_user_id() -> str:
    """Return the current user id.

    Demo phase returns `settings.DEFAULT_USER_ID`. A future JWT upgrade only
    replaces the body of this function.
    """
    return settings.DEFAULT_USER_ID
