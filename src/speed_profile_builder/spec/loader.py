"""Load, merge and validate profile specs.

Three jobs live here, in order:

1. **Parse** YAML while remembering where every key came from, so validation
   errors can name a line number instead of a pydantic tuple.
2. **Resolve inheritance** — ``extends:`` chains are flattened depth-first with
   documented merge semantics (see :func:`deep_merge`).
3. **Validate** the flattened document against
   :class:`~speed_profile_builder.spec.schema.ProfileSpec`, translating pydantic
   errors into located :class:`~speed_profile_builder.errors.SpecIssue` objects.

Nothing in this module knows about routing engines.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..errors import SpecError, SpecIssue, UnitError
from .schema import ProfileSpec

#: Directory holding the bundled base profiles shipped with the package.
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "profiles"

#: Collections merged by identity key rather than replaced wholesale.
KEYED_LISTS: dict[str, str] = {"zones": "id", "time_factors": "name"}

_MAX_EXTENDS_DEPTH = 16


class LineIndex:
    """Maps dotted spec paths to ``(line, column)`` in the source document.

    Built from the YAML *node* tree rather than the loaded objects, because by
    the time PyYAML has produced dicts the positions are gone. Only mapping keys
    and sequence indices are recorded — that is enough to point a user at the
    right line.
    """

    def __init__(self, source: Path | None = None) -> None:
        self.source = source
        self._positions: dict[str, tuple[int, int]] = {}

    @classmethod
    def from_text(cls, text: str, source: Path | None = None) -> LineIndex:
        """Compose ``text`` into nodes and record the position of every path."""
        index = cls(source)
        try:
            node = yaml.compose(text)
        except yaml.YAMLError:
            return index
        if node is not None:
            index._walk(node, "")
        return index

    def _walk(self, node: yaml.Node, path: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key = str(getattr(key_node, "value", key_node))
                child = f"{path}.{key}" if path else key
                self._positions[child] = (
                    key_node.start_mark.line + 1,
                    key_node.start_mark.column + 1,
                )
                self._walk(value_node, child)
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                child = f"{path}.{i}" if path else str(i)
                self._positions[child] = (item.start_mark.line + 1, item.start_mark.column + 1)
                self._walk(item, child)

    def locate(self, path: str) -> tuple[int | None, int | None]:
        """Return the position of ``path``, falling back to its nearest parent.

        Falling back matters because pydantic reports errors at leaves that may
        not exist in the document at all (a missing required key), and pointing
        at the enclosing block is still far better than pointing nowhere.
        """
        parts = path.split(".") if path else []
        while parts:
            candidate = ".".join(parts)
            if candidate in self._positions:
                return self._positions[candidate]
            parts.pop()
        return (None, None)

    def keys(self) -> list[str]:
        """All recorded paths; used by tests and by unknown-key suggestions."""
        return sorted(self._positions)


def deep_merge(base: Any, override: Any, path: str = "") -> Any:
    """Merge ``override`` onto ``base`` with profile-friendly semantics.

    The rules are deliberately explicit, because "how does my override behave"
    is the single most common question when profiles inherit:

    * **Mappings** merge recursively, key by key.
    * **Scalars** replace.
    * A value of ``null`` **removes** the inherited key entirely. This is the
      only way to unset something a base profile set.
    * **Plain lists** replace wholesale. Element-wise merging of an unkeyed list
      is never what anyone means.
    * **Keyed lists** (``zones`` by ``id``, ``time_factors`` by ``name``) merge
      by that key, preserving base order and appending new entries. An entry
      with ``remove: true`` deletes the inherited entry of the same key.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            child = f"{path}.{key}" if path else key
            if value is None:
                out.pop(key, None)
                continue
            if key in KEYED_LISTS and isinstance(value, list) and isinstance(out.get(key), list):
                out[key] = _merge_keyed_list(out[key], value, KEYED_LISTS[key], child)
            else:
                out[key] = deep_merge(out.get(key), value, child)
        return out
    return override


def _merge_keyed_list(
    base: list[Any], override: list[Any], key: str, path: str
) -> list[dict[str, Any]]:
    """Merge two lists of mappings by their identity ``key``."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source in (base, override):
        for i, item in enumerate(source):
            if not isinstance(item, dict) or key not in item:
                raise SpecError(
                    SpecIssue(
                        message=(
                            f"every entry in '{path.rsplit('.', 1)[0] or path}' must be a mapping "
                            f"with a {key!r} field so overrides can target it"
                        ),
                        path=f"{path}.{i}",
                    )
                )
            ident = str(item[key])
            if item.get("remove") is True:
                merged.pop(ident, None)
                if ident in order:
                    order.remove(ident)
                continue
            if ident in merged:
                merged[ident] = deep_merge(merged[ident], item, path)
            else:
                merged[ident] = dict(item)
                order.append(ident)
    return [merged[i] for i in order]


def load_yaml(path: Path) -> tuple[dict[str, Any], LineIndex]:
    """Read one YAML document and its line index, with located parse errors."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(SpecIssue(message=f"cannot read spec: {exc}", source=path)) from exc

    index = LineIndex.from_text(text, path)
    try:
        data = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        raise SpecError(
            SpecIssue(
                message=f"invalid YAML: {exc.problem or exc.context}",
                line=(mark.line + 1) if mark else None,
                column=(mark.column + 1) if mark else None,
                source=path,
            )
        ) from exc
    except yaml.YAMLError as exc:
        raise SpecError(SpecIssue(message=f"invalid YAML: {exc}", source=path)) from exc

    if data is None:
        raise SpecError(SpecIssue(message="spec file is empty", source=path))
    if not isinstance(data, dict):
        raise SpecError(
            SpecIssue(
                message=f"spec must be a YAML mapping, got {type(data).__name__}",
                source=path,
            )
        )
    return data, index


def bundled_profiles() -> dict[str, Path]:
    """Return ``{name: path}`` for every base profile shipped in the package."""
    if not BUNDLED_DIR.is_dir():  # pragma: no cover - packaging failure
        return {}
    return {p.stem: p for p in sorted(BUNDLED_DIR.glob("*.yaml"))}


def _resolve_extends(name: str, origin: Path | None) -> Path:
    """Locate the parent named by ``extends``, preferring a sibling file.

    Local files win over bundled names so a team can vendor and edit a base
    profile without renaming every child spec.
    """
    if origin is not None:
        for candidate in (
            origin.parent / name,
            origin.parent / f"{name}.yaml",
            origin.parent / f"{name}.yml",
        ):
            if candidate.is_file():
                return candidate
    bundled = bundled_profiles()
    if name in bundled:
        return bundled[name]
    known = ", ".join(sorted(bundled))
    suggestion = difflib.get_close_matches(name, list(bundled), n=1)
    hint = f"did you mean {suggestion[0]!r}?" if suggestion else f"bundled profiles: {known}"
    raise SpecError(
        SpecIssue(
            message=f"extends: {name!r} does not name a local file or a bundled profile",
            path="extends",
            source=origin,
            hint=hint,
        )
    )


def flatten(path: Path) -> tuple[dict[str, Any], LineIndex, list[Path]]:
    """Resolve the full ``extends`` chain rooted at ``path``.

    Returns the merged document, the line index of the *leaf* spec (where the
    user's own edits live), and the chain from root base to leaf.
    """
    chain: list[Path] = []
    documents: list[dict[str, Any]] = []
    leaf_index: LineIndex | None = None
    seen: set[Path] = set()

    current: Path | None = path
    while current is not None:
        resolved = current.resolve()
        if resolved in seen:
            names = " -> ".join(p.name for p in [*chain, resolved])
            raise SpecError(
                SpecIssue(
                    message=f"circular 'extends' chain: {names}",
                    path="extends",
                    source=resolved,
                )
            )
        seen.add(resolved)
        data, index = load_yaml(resolved)
        if leaf_index is None:
            leaf_index = index
        documents.append(data)
        chain.append(resolved)
        if len(chain) > _MAX_EXTENDS_DEPTH:
            raise SpecError(
                SpecIssue(
                    message=f"'extends' chain deeper than {_MAX_EXTENDS_DEPTH} levels",
                    path="extends",
                    source=resolved,
                )
            )
        parent = data.get("extends")
        if parent is None:
            current = None
        elif not isinstance(parent, str):
            raise SpecError(
                SpecIssue(
                    message=f"extends must be a string, got {type(parent).__name__}",
                    path="extends",
                    source=resolved,
                )
            )
        else:
            current = _resolve_extends(parent, resolved)

    merged: dict[str, Any] = {}
    for document in reversed(documents):
        merged = deep_merge(merged, document)

    # The leaf's own identity wins; inheriting a base's name would silently
    # overwrite the base's generated files.
    merged["name"] = documents[0].get("name", merged.get("name"))
    merged["extends"] = documents[0].get("extends")
    assert leaf_index is not None
    return merged, leaf_index, list(reversed(chain))


def _describe_pydantic_error(err: dict[str, Any]) -> SpecIssue:
    """Turn one pydantic error dict into a located, human-readable issue."""
    path = ".".join(str(p) for p in err["loc"])
    kind = err["type"]
    message = err["msg"]
    hint = ""

    if kind == "extra_forbidden":
        parent_path = path.rsplit(".", 1)[0] if "." in path else ""
        key = path.rsplit(".", 1)[-1]
        siblings = _siblings_for(parent_path)
        close = difflib.get_close_matches(key, siblings, n=1)
        message = f"unknown key {key!r}"
        hint = f"did you mean {close[0]!r}?" if close else "remove it or check the spec reference"
    elif kind == "missing":
        message = "required key is missing"
    elif kind.startswith("value_error"):
        message = message.removeprefix("Value error, ")
    elif kind == "literal_error":
        message = f"{message}; got {err.get('input')!r}"

    return SpecIssue(message=message, path=path, hint=hint)


def _siblings_for(parent_path: str) -> list[str]:
    """Field names valid next to ``parent_path``, for did-you-mean suggestions."""
    model: Any = ProfileSpec
    if parent_path:
        for part in parent_path.split("."):
            fields = getattr(model, "model_fields", {})
            if part not in fields:
                return []
            annotation = fields[part].annotation
            model = _unwrap_model(annotation)
            if model is None:
                return []
    return list(getattr(model, "model_fields", {}))


def _unwrap_model(annotation: Any) -> Any:
    """Best-effort extraction of a BaseModel class from a field annotation."""
    if hasattr(annotation, "model_fields"):
        return annotation
    for arg in getattr(annotation, "__args__", ()):
        found = _unwrap_model(arg)
        if found is not None:
            return found
    return None


def validate_document(
    document: dict[str, Any], index: LineIndex | None = None, source: Path | None = None
) -> ProfileSpec:
    """Validate an already-flattened document, raising a located `SpecError`."""
    index = index or LineIndex(source)
    try:
        return ProfileSpec.model_validate(document)
    except ValidationError as exc:
        issues = []
        for err in exc.errors():
            issue = _describe_pydantic_error(err)
            issue.line, issue.column = index.locate(issue.path)
            issue.source = index.source or source
            issues.append(issue)
        raise SpecError(issues, f"{len(issues)} problem(s) validating profile spec") from exc
    except UnitError as exc:
        raise SpecError(SpecIssue(message=str(exc), source=index.source or source)) from exc


def load_spec(path: Path) -> tuple[ProfileSpec, list[Path]]:
    """Load, flatten and validate a spec file. The main entry point of this layer.

    Returns the validated spec and the resolved inheritance chain (base first),
    which the emitters record in their generated header for provenance.
    """
    document, index, chain = flatten(Path(path))
    return validate_document(document, index, Path(path)), chain


def load_bundled(name: str) -> ProfileSpec:
    """Load a bundled base profile by name.

    :raises SpecError: if no bundled profile has that name.
    """
    profiles = bundled_profiles()
    if name not in profiles:
        close = difflib.get_close_matches(name, list(profiles), n=1)
        raise SpecError(
            SpecIssue(
                message=f"no bundled profile named {name!r}",
                hint=(
                    f"did you mean {close[0]!r}?"
                    if close
                    else f"available: {', '.join(sorted(profiles))}"
                ),
            )
        )
    return load_spec(profiles[name])[0]
