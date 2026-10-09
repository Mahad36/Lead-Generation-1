import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

API_ROOT = Path(__file__).resolve().parents[1]
# Repo root (for `workers`). In Docker the API lives at /app, which has no grandparent.
ROOT = API_ROOT.parents[1] if len(API_ROOT.parents) > 1 else API_ROOT.parent
for path in (str(API_ROOT), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.config import get_settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import *  # noqa: E402, F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# Percent-encode sequences in passwords must be escaped for ConfigParser.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
