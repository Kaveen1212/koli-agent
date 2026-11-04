"""Static SQL-injection guard over the app source.

ruff's S608 (pyproject.toml) catches a SQL statement assembled with an
f-string, ``+``, ``%`` or ``.format()``. It misses a fragment appended to a
query built elsewhere, such as ``sql += f" AND user_id = '{uid}'"``, because
the fragment alone does not look like a statement. This closes that gap: no
string under app/ that is formatted with a value may contain SQL keywords.
Values go in as bind parameters: ``text("... AND user_id = :uid")`` with
``{"uid": uid}``.
"""
import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# Upper case only: SQL in this codebase is written that way, prose is not.
SQL = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|AND|OR|SET|VALUES|FROM|JOIN|"
    r"ORDER BY|GROUP BY|LIMIT|OFFSET|RETURNING|HAVING|UNION)\b"
)


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _add_chain(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_chain(node.left) + _add_chain(node.right)
    return [node]


def _formatted_sql(node: ast.AST) -> str | None:
    """The literal text of a string built from values, when it reads as SQL."""
    if isinstance(node, ast.JoinedStr):
        if any(isinstance(v, ast.FormattedValue) for v in node.values):
            text = "".join(v.value for v in node.values if _is_str(v))
            return text if SQL.search(text) else None
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and _is_str(node.left):
        return node.left.value if SQL.search(node.left.value) else None
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
          and node.func.attr == "format" and _is_str(node.func.value)):
        return node.func.value.value if SQL.search(node.func.value.value) else None
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # "... WHERE id = '" + value: SQL text directly followed by a value.
        # (sql + " AND x = :y" is fine: the constant comes last.)
        parts = _add_chain(node)
        for here, after in zip(parts, parts[1:]):
            if _is_str(here) and SQL.search(here.value) and not _is_str(after):
                return here.value
    return None


def find_sql_built_from_values(root: Path = APP) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # A chain a + b + c nests, so one line can match more than once.
        hits = {}
        for node in ast.walk(tree):
            sql = _formatted_sql(node)
            if sql is not None:
                hits.setdefault(node.lineno, sql)
        found += [f"{path.relative_to(root.parent)}:{line}: {sql.strip()[:80]!r}"
                  for line, sql in sorted(hits.items())]
    return found


def test_app_never_formats_values_into_sql():
    found = find_sql_built_from_values()
    assert found == [], (
        "SQL built from values (use :name bind parameters instead):\n" + "\n".join(found)
    )


def test_guard_catches_every_way_of_formatting_sql(tmp_path):
    (tmp_path / "probe.py").write_text(
        "from sqlalchemy import text\n"
        "def bad(db, gid, uid, sql):\n"
        "    db.execute(text(f\"SELECT * FROM generations WHERE id = '{gid}'\"))\n"
        "    sql += f\" AND user_id = '{uid}'\"\n"
        "    db.execute(text(\"DELETE FROM generations WHERE id = '%s'\" % gid))\n"
        "    db.execute(text(\"UPDATE generations SET status = '{}'\".format(gid)))\n"
        "    sql += \" AND user_id = '\" + uid + \"'\"\n"
        "def good(db, gid, uid, sql):\n"
        "    db.execute(text('SELECT * FROM generations WHERE id = :id'), {'id': gid})\n"
        "    sql += ' AND user_id = :uid'\n"
        "    sql = sql + ' ORDER BY created_at'\n"
        "    print(f'Generation {gid} failed for user {uid}')\n"
    )
    found = find_sql_built_from_values(tmp_path)
    assert [f.split(":")[1] for f in found] == ["3", "4", "5", "6", "7"]
