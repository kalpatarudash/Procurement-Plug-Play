"""Dataset-level settings that change when a user imports their own data.

`as_of` ("today" for the dataset), `currency` and `fiscal_year_start_month` default to the
values in config.Settings (env / .env). An import stores overrides in the settings table; they
are applied to the shared `settings` object at startup and right after an import or demo reset.
"""

from datetime import date

from sqlalchemy.orm import Session

from .config import Settings, settings
from .models import Setting

KEYS = ("as_of", "currency", "fiscal_year_start_month")
_defaults = Settings()


def apply(db: Session) -> None:
    for key in KEYS:
        row = db.get(Setting, key)
        if row is None:
            setattr(settings, key, getattr(_defaults, key))
        elif key == "as_of":
            settings.as_of = date.fromisoformat(row.value)
        elif key == "fiscal_year_start_month":
            settings.fiscal_year_start_month = int(row.value)
        else:
            setattr(settings, key, row.value)


def store(db: Session, values: dict[str, object]) -> None:
    """Save overrides (None removes one, restoring the default) without committing."""
    for key, value in values.items():
        if key not in KEYS:
            raise KeyError(key)
        row = db.get(Setting, key)
        if value is None:
            if row is not None:
                db.delete(row)
            continue
        text = value.isoformat() if isinstance(value, date) else str(value)
        if row is None:
            db.add(Setting(key=key, value=text))
        else:
            row.value = text
