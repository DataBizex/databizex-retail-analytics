"""Read-only guard for the custom SQL editor. No Qt here.

check_sql accepts one SELECT (or WITH ... SELECT) statement and returns it without
comments or a trailing semicolon; anything else raises GuardError with a message a
user can act on. Keywords are matched as whole words outside string literals,
quoted identifiers and comments, so SELECT 'DROP' runs but SELECT 1; DROP ... does not.
"""

from __future__ import annotations

import re

BLOCKED_WORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "ATTACH", "DETACH", "COPY", "EXPORT",
    "IMPORT", "INSTALL", "LOAD", "PRAGMA", "SET", "CALL", "VACUUM", "TRUNCATE", "GRANT", "BEGIN", "COMMIT",
)
ALLOWED_FIRST_WORDS = ("SELECT", "WITH")
READ_ONLY = "Only read-only SELECT queries can run here."

_BLOCKED = re.compile(r"\b(" + "|".join(BLOCKED_WORDS) + r")\b", re.IGNORECASE)
_FIRST_WORD = re.compile(r"[A-Za-z_]+")


class GuardError(Exception):
    """Raised with a plain-language reason when a query may not run."""


def _split(sql: str) -> tuple[str, str]:
    """(sql without comments, the same with quoted text blanked) for scanning.

    Comments become a space; string literals ('...') and quoted identifiers ("...")
    stay in the first string and become '' / "" in the second. A doubled quote inside
    them is an escaped quote.
    """
    runnable: list[str] = []
    scan: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            runnable.append(" ")
            scan.append(" ")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0:
                raise GuardError("A /* comment is not closed. Add */ at its end or remove it.")
            i = end + 2
            runnable.append(" ")
            scan.append(" ")
        elif ch in ("'", '"'):
            j = i + 1
            while True:
                j = sql.find(ch, j)
                if j < 0:
                    what = "A text value in single quotes" if ch == "'" else "A name in double quotes"
                    raise GuardError(f"{what} is not closed. Check the quotes in the query.")
                if sql.startswith(ch * 2, j):  # escaped quote
                    j += 2
                    continue
                break
            runnable.append(sql[i:j + 1])
            scan.append(ch * 2)
            i = j + 1
        else:
            runnable.append(ch)
            scan.append(ch)
            i += 1
    return "".join(runnable), "".join(scan)


def check_sql(sql: str) -> str:
    """The statement ready to run (comments and one trailing semicolon removed), or GuardError."""
    runnable, scan = _split(sql or "")
    runnable, scan = runnable.strip(), scan.strip()
    if scan.endswith(";"):
        runnable, scan = runnable[:-1].rstrip(), scan[:-1].rstrip()
    if not scan:
        raise GuardError("The query is empty. Type a SELECT query, for example: SELECT * FROM sales LIMIT 100")
    if ";" in scan:
        raise GuardError(f"{READ_ONLY} Run one statement at a time: remove the extra semicolon "
                         "and everything after it.")
    first = _FIRST_WORD.match(scan)
    if not first or first.group(0).upper() not in ALLOWED_FIRST_WORDS:
        raise GuardError(f"{READ_ONLY} The query must start with SELECT or WITH.")
    blocked = _BLOCKED.search(scan)
    if blocked:
        raise GuardError(f"{READ_ONLY} The word {blocked.group(1).upper()} is not allowed.")
    return runnable
