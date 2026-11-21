"""The server's translation catalogues, without GNU gettext.

Dutch is the source language: every user-facing text is a Dutch msgid inside a gettext
call (or `{% translate %}` in a template). The English catalogue lives in
`locale/en/LC_MESSAGES/django.po`, with its compiled `django.mo` next to it; both are
committed. `manage.py update_messages` reads the msgids from the source (with `ast`, so
no xgettext is needed), merges them into the `.po` (a known translation stays, a new
msgid gets an empty one, a msgid no longer used goes) and writes the `.mo`.
`update_messages --check` and tests/test_i18n_catalogue.py fail when the files are out
of date or a msgid has no translation.
"""

import ast
import re
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# The gettext functions of `django.utils.translation` whose first argument is a msgid
# (and, for the plural ones, whose second is the plural msgid).
_SINGULAR = frozenset({"gettext", "gettext_lazy", "gettext_noop"})
_PLURAL = frozenset({"ngettext", "ngettext_lazy"})
_TEMPLATE_TAG = re.compile(r"""\{%\s*(?:translate|trans)\s+(["'])(.*?)\1\s*(?:noop\s*)?%\}""")
# Every `%(name)s` placeholder, and a bare `%s`/`%d`.
_PLACEHOLDER = re.compile(r"%(?:\([a-z_]+\))?[sd]")

HEADER = (
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
    "Language: {language}\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\n"
)


@dataclass
class Message:
    msgid: str
    plural: str = ""
    # msgstr, or msgstr[0] and msgstr[1] for a plural.
    translations: list[str] = field(default_factory=list)
    # The source files that use it (relative paths), for the translator.
    files: list[str] = field(default_factory=list)

    @property
    def translated(self) -> bool:
        wanted = 2 if self.plural else 1
        return len(self.translations) == wanted and all(t.strip() for t in self.translations)


def placeholders(text: str) -> list[str]:
    """The `%(name)s` placeholders of a text, sorted: a translation keeps the same ones."""
    return sorted(_PLACEHOLDER.findall(text))


def extract(root: Path, folders: Sequence[str] = ("apps", "brievie")) -> list[Message]:
    """The msgids of the Python files and templates under `root`'s `folders`, in order
    of file path and line, each once. Migrations hold no user-facing text."""
    found: dict[tuple[str, str], Message] = {}
    for folder in folders:
        for path in sorted((root / folder).rglob("*")):
            if "migrations" in path.parts or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix == ".py":
                pairs = _from_python(path.read_text(encoding="utf-8"))
            elif path.suffix == ".html":
                pairs = [(m.group(2), "") for m in _TEMPLATE_TAG.finditer(path.read_text("utf-8"))]
            else:
                continue
            for msgid, plural in pairs:
                message = found.setdefault((msgid, plural), Message(msgid, plural))
                if relative not in message.files:
                    message.files.append(relative)
    return list(found.values())


def _from_python(source: str) -> list[tuple[str, str]]:
    tree = ast.parse(source)
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "django.utils.translation":
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    calls: list[tuple[int, int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        function = names.get(node.func.id)
        if function in _SINGULAR and node.args:
            msgid = _literal(node.args[0])
            if msgid is not None:
                calls.append((node.lineno, node.col_offset, msgid, ""))
        elif function in _PLURAL and len(node.args) >= 2:
            singular, plural = _literal(node.args[0]), _literal(node.args[1])
            if singular is not None and plural is not None:
                calls.append((node.lineno, node.col_offset, singular, plural))
    return [(msgid, plural) for _line, _col, msgid, plural in sorted(calls)]


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def non_literal_calls(root: Path, folders: Sequence[str] = ("apps", "brievie")) -> list[str]:
    """`file:line` of every gettext call whose msgid is not a plain string: such a text
    can never be in the catalogue (an f-string, a variable)."""
    found: list[str] = []
    for folder in folders:
        for path in sorted((root / folder).rglob("*.py")):
            if "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "django.utils.translation"
                for alias in node.names
                if alias.name in _SINGULAR | _PLURAL
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in names
                    and (not node.args or _literal(node.args[0]) is None)
                ):
                    found.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return found


# --- .po -------------------------------------------------------------------------------


def read_po(text: str) -> list[Message]:
    """The entries of a .po file in the form `write_po` writes: one entry per block of
    lines, blocks apart by an empty line. The header (empty msgid) is left out."""
    messages: list[Message] = []
    for block in re.split(r"\n\s*\n", text):
        message = Message("")
        target: tuple[str, int] | None = None
        for raw in block.splitlines():
            line = raw.strip()
            if line.startswith("#:"):
                message.files.extend(line[2:].split())
            elif not line or line.startswith("#"):
                continue
            elif line.startswith('"') and target is not None:
                _append(message, target, _unquote(line))
            else:
                keyword, _, rest = line.partition(" ")
                if keyword == "msgid":
                    target = ("msgid", 0)
                elif keyword == "msgid_plural":
                    target = ("plural", 0)
                elif keyword == "msgstr":
                    target = ("msgstr", 0)
                elif re.fullmatch(r"msgstr\[[01]\]", keyword):
                    target = ("msgstr", int(keyword[7]))
                else:
                    raise ValueError(f"Unexpected line in .po: {raw!r}")
                if target[0] == "msgstr":
                    while len(message.translations) <= target[1]:
                        message.translations.append("")
                _append(message, target, _unquote(rest))
        if message.msgid:
            messages.append(message)
    return messages


def _append(message: Message, target: tuple[str, int], text: str) -> None:
    kind, index = target
    if kind == "msgid":
        message.msgid += text
    elif kind == "plural":
        message.plural += text
    else:
        message.translations[index] += text


def write_po(messages: Iterable[Message], *, language: str) -> str:
    lines = ['msgid ""', f"msgstr {_quote(HEADER.format(language=language))}", ""]
    for message in messages:
        if message.files:
            lines.append("#: " + " ".join(message.files))
        if "%" in message.msgid or "%" in message.plural:
            lines.append("#, python-format")
        lines.append(f"msgid {_quote(message.msgid)}")
        if message.plural:
            lines.append(f"msgid_plural {_quote(message.plural)}")
            translations = [*message.translations, "", ""][:2]
            lines.extend(f"msgstr[{i}] {_quote(t)}" for i, t in enumerate(translations))
        else:
            translation = message.translations[0] if message.translations else ""
            lines.append(f"msgstr {_quote(translation)}")
        lines.append("")
    return "\n".join(lines)


def merge(source: Sequence[Message], existing: Sequence[Message]) -> list[Message]:
    """The source's msgids in source order, with the translations `existing` has."""
    known = {(m.msgid, m.plural): m.translations for m in existing}
    return [
        Message(m.msgid, m.plural, list(known.get((m.msgid, m.plural), [])), list(m.files))
        for m in source
    ]


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) < 2 or token[0] != '"' or token[-1] != '"':
        raise ValueError(f"Not a quoted .po string: {token!r}")
    out: list[str] = []
    chars = iter(token[1:-1])
    for char in chars:
        if char == "\\":
            escaped = next(chars, "")
            if escaped not in _ESCAPES:
                raise ValueError(f"Unknown escape in .po string: \\{escaped}")
            out.append(_ESCAPES[escaped])
        else:
            out.append(char)
    return "".join(out)


# --- .mo -------------------------------------------------------------------------------


def write_mo(messages: Iterable[Message], *, language: str) -> bytes:
    """The GNU .mo file of the translated messages (as CPython's Tools/i18n/msgfmt.py
    writes it: sorted keys, no hash table). An untranslated message is left out, so
    gettext falls back to the Dutch msgid."""
    entries: dict[bytes, bytes] = {b"": HEADER.format(language=language).encode()}
    for message in messages:
        if not message.translated:
            continue
        if message.plural:
            key = f"{message.msgid}\0{message.plural}".encode()
            entries[key] = "\0".join(message.translations[:2]).encode()
        else:
            entries[message.msgid.encode()] = message.translations[0].encode()
    keys = sorted(entries)
    ids = b"".join(key + b"\0" for key in keys)
    strs = b"".join(entries[key] + b"\0" for key in keys)
    offsets: list[tuple[int, int, int, int]] = []
    id_at = str_at = 0
    for key in keys:
        offsets.append((id_at, len(key), str_at, len(entries[key])))
        id_at += len(key) + 1
        str_at += len(entries[key]) + 1
    count = len(keys)
    key_table_at = 7 * 4
    value_table_at = key_table_at + count * 8
    ids_at = value_table_at + count * 8
    strs_at = ids_at + len(ids)
    key_table: list[int] = []
    value_table: list[int] = []
    for id_offset, id_length, str_offset, str_length in offsets:
        key_table += [id_length, ids_at + id_offset]
        value_table += [str_length, strs_at + str_offset]
    header = struct.pack("Iiiiiii", 0x950412DE, 0, count, key_table_at, value_table_at, 0, 0)
    return (
        header
        + struct.pack(f"{len(key_table)}i", *key_table)
        + struct.pack(f"{len(value_table)}i", *value_table)
        + ids
        + strs
    )
