from sqlalchemy import inspect, text

from src.models import audit, backfills, cases, reviews, runs, users  # noqa: F401  Register the tables.
from src.models.base import metadata
from src.models.reviews import reviews as reviews_table
from src.models.runs import runs as runs_table


def _has_column(db, table, column):
    return column in {item["name"] for item in inspect(db).get_columns(table)}


def _default_literal(value):
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _add_column(db, table, column):
    if not _has_column(db, table, column.name):
        db.execute(text(
            f'ALTER TABLE {table} ADD COLUMN {column.name} '
            f'{column.type.compile(db.dialect)} NOT NULL DEFAULT '
            f'{_default_literal(column.server_default.arg)}'
        ))


def upgrade(connection):
    # Version 2 stays additive: new columns keep old rows valid, backfills live in their own table.
    _add_column(connection, "runs", runs_table.c.version)
    _add_column(connection, "runs", runs_table.c.revision)
    _add_column(connection, "reviews", reviews_table.c.result_version)
    _add_column(connection, "reviews", reviews_table.c.result_digest)
    metadata.tables["receipt_backfills"].create(connection, checkfirst=True)
