"""Imports every module's models so Base.metadata is complete (Alembic and tests use this)."""

from app.modules.accounts import models as accounts  # noqa: F401
from app.modules.assistant import models as assistant  # noqa: F401
from app.modules.cart import models as cart  # noqa: F401
from app.modules.catalog import models as catalog  # noqa: F401
from app.modules.orders import models as orders  # noqa: F401
from app.modules.payments import models as payments  # noqa: F401
from app.platform import models as platform  # noqa: F401
from app.shared.base import Base

metadata = Base.metadata
