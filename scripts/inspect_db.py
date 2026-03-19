import argparse
import os
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = "data/jung_hybrid.db"


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def print_table_summary(cursor: sqlite3.Cursor) -> None:
    print("--- Table Summary ---")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [row["name"] for row in cursor.fetchall()]

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) AS count FROM \"{table}\"")
        count = cursor.fetchone()["count"]
        print(f"{table}: {count} rows")


def print_schema(cursor: sqlite3.Cursor, table_name: str) -> None:
    print(f"\n--- Schema: {table_name} ---")
    cursor.execute(f"PRAGMA table_info(\"{table_name}\");")
    columns = cursor.fetchall()

    if not columns:
        print("Table not found.")
        return

    for column in columns:
        print(dict(column))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect a local SQLite database in read-only mode.",
    )
    parser.add_argument(
        "--db-path",
        default=os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH),
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--schema",
        nargs="*",
        default=[],
        metavar="TABLE",
        help="Optional table names whose schema should be printed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path)

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    connection = connect_read_only(db_path)
    cursor = connection.cursor()

    try:
        print_table_summary(cursor)

        for table_name in args.schema:
            print_schema(cursor, table_name)
    finally:
        connection.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
