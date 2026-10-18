from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name:
    # 不能禁用已存在的 app.* logger：应用启动时先配置了业务日志，
    # 随后由 init_db() 跑迁移；fileConfig 默认会把它们全部 disabled，
    # 导致服务启动后的会话事件不再写入轮转日志。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")),
)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
