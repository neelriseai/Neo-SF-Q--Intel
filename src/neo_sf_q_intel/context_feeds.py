from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field

KNOWLEDGE_DIRECTORY_NAME = "knowledge-repo"
KNOWLEDGE_GROUPS = ("pages", "modules", "impact", "root")
SELECTOR_GROUPS = {"page": "pages", "module": "modules", "impact": "impact"}
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
INDEX_HEADING_PATTERN = re.compile(r"^#{1,3}\s+(\S.*?)\s*$")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
MAXIMUM_SECTION_CHARACTERS = 8000
MAXIMUM_GRAPH_EDGES = 500
DEFAULT_RELATION = "related"
API_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")
NAMESPACE_SUFFIX = "}"
MAXIMUM_FIELD_METADATA_BYTES = 262_144
MAXIMUM_PICKLIST_VALUES = 200
FIELD_METADATA_SUFFIX = ".field-meta.xml"
FIELD_METADATA_SEGMENTS = ("force-app", "main", "default", "objects")


class ContextFeedError(RuntimeError):
    """Raised when a scoped context feed request cannot be served safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ContextFeedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class KnowledgeDocument(ContextFeedModel):
    key: str
    path: str
    headings: list[str] = Field(default_factory=list)


class KnowledgeIndex(ContextFeedModel):
    pages: list[KnowledgeDocument] = Field(default_factory=list)
    modules: list[KnowledgeDocument] = Field(default_factory=list)
    impact: list[KnowledgeDocument] = Field(default_factory=list)
    root: list[KnowledgeDocument] = Field(default_factory=list)


class KnowledgeSection(ContextFeedModel):
    path: str
    section: str | None = None
    body: str
    chars: int
    truncated: bool


class GraphNeighborhood(ContextFeedModel):
    seed: str
    hops: int
    edges: list[str] = Field(default_factory=list)
    edge_count: int = Field(alias="edgeCount")
    truncated: bool


class PicklistValue(ContextFeedModel):
    value: str
    label: str
    default: bool = False


class FieldMetadata(ContextFeedModel):
    """Declared identity of one custom field, read from the versioned source project.

    The label is carried in the clear on purpose. It is schema authored in our own repository,
    not org record data, and the healing model needs it because the label is what the rendered
    page actually shows. Record values stay out of this feed entirely.
    """

    object_api_name: str = Field(alias="objectApiName")
    field_api_name: str = Field(alias="fieldApiName")
    field_type: str | None = Field(default=None, alias="type")
    label: str | None = None
    required: bool = False
    reference_to: list[str] = Field(default_factory=list, alias="referenceTo")
    relationship_name: str | None = Field(default=None, alias="relationshipName")
    picklist_values: list[PicklistValue] = Field(default_factory=list, alias="picklistValues")
    picklist_truncated: bool = Field(default=False, alias="picklistTruncated")
    restricted_picklist: bool | None = Field(default=None, alias="restrictedPicklist")
    value_set_name: str | None = Field(default=None, alias="valueSetName")
    source_path: str = Field(alias="sourcePath")


def knowledge_index(repo_root: Path) -> KnowledgeIndex:
    """Group every knowledge-repo markdown document by its top level folder."""
    root = _repo_root(repo_root)
    knowledge_root = _knowledge_root(repo_root)
    grouped: dict[str, list[KnowledgeDocument]] = {group: [] for group in KNOWLEDGE_GROUPS}
    if knowledge_root.is_dir():
        for path in sorted(knowledge_root.rglob("*.md")):
            if not path.is_file():
                continue
            relative = path.relative_to(knowledge_root)
            group = "root" if len(relative.parts) == 1 else relative.parts[0]
            if group not in grouped:
                continue
            grouped[group].append(
                KnowledgeDocument(
                    key=path.stem,
                    path=_relative_posix(root, path),
                    headings=_document_headings(_read_document(path)),
                )
            )
    return KnowledgeIndex(**grouped)


def knowledge_section(
    repo_root: Path,
    *,
    page: str | None = None,
    module: str | None = None,
    impact: str | None = None,
    section: str | None = None,
) -> KnowledgeSection:
    """Return one knowledge document body, or a single heading block inside it."""
    selectors = {"page": page, "module": module, "impact": impact}
    chosen = [name for name, value in selectors.items() if value is not None]
    if len(chosen) != 1:
        raise ContextFeedError("SELECTOR_INVALID")
    selector = chosen[0]
    key = selectors[selector]
    if key is None or KEY_PATTERN.match(key) is None:
        raise ContextFeedError("PATH_OUTSIDE_REPO")
    knowledge_root = _knowledge_root(repo_root)
    document = (knowledge_root / SELECTOR_GROUPS[selector] / f"{key}.md").resolve()
    if not document.is_relative_to(knowledge_root):
        raise ContextFeedError("PATH_OUTSIDE_REPO")
    if not document.is_file():
        raise ContextFeedError("SECTION_NOT_FOUND")
    text = _read_document(document)
    body = text if section is None else _section_body(text, section)
    truncated = len(body) > MAXIMUM_SECTION_CHARACTERS
    if truncated:
        body = body[:MAXIMUM_SECTION_CHARACTERS]
    return KnowledgeSection(
        path=_relative_posix(_repo_root(repo_root), document),
        section=section,
        body=body,
        chars=len(body),
        truncated=truncated,
    )


def graph_neighborhood(
    graph_path: Path,
    entity_id: str,
    *,
    hops: int = 1,
    maximum_edges: int = 120,
) -> GraphNeighborhood:
    """Render the deterministic edge neighborhood around one application graph entity."""
    if hops not in (1, 2):
        raise ContextFeedError("HOPS_INVALID")
    if not 1 <= maximum_edges <= MAXIMUM_GRAPH_EDGES:
        raise ContextFeedError("LIMIT_INVALID")
    payload = _load_graph(graph_path)
    edges = _normalised_edges(payload.get("edges", []))
    frontier = {entity_id}
    selected: set[tuple[str, str, str]] = set()
    for _ in range(hops):
        touched = [edge for edge in edges if edge[0] in frontier or edge[2] in frontier]
        if not touched:
            break
        selected.update(touched)
        frontier = frontier.union({edge[0] for edge in touched}, {edge[2] for edge in touched})
    rendered = sorted({f"{left} -> {relation} -> {right}" for left, relation, right in selected})
    truncated = len(rendered) > maximum_edges
    if truncated:
        rendered = rendered[:maximum_edges]
    return GraphNeighborhood(
        seed=entity_id,
        hops=hops,
        edges=rendered,
        edgeCount=len(rendered),
        truncated=truncated,
    )


def metadata_lookup(
    salesforce_root: Path,
    object_api_name: str,
    field_api_name: str,
) -> FieldMetadata:
    """Read one field declaration from the configured Salesforce source project.

    Source of truth is the versioned metadata, never a live describe call: the healing path runs
    inside an ephemeral browser session, and reaching back to the org from here would widen that
    session scope. A field the project does not declare is reported absent so that the caller
    abstains rather than guessing.
    """
    if not API_NAME_PATTERN.fullmatch(object_api_name):
        raise ContextFeedError("OBJECT_NAME_INVALID")
    if not API_NAME_PATTERN.fullmatch(field_api_name):
        raise ContextFeedError("FIELD_NAME_INVALID")
    root = Path(salesforce_root)
    if not root.is_dir():
        raise ContextFeedError("SALESFORCE_ROOT_UNAVAILABLE")
    root = root.resolve()
    path = root.joinpath(
        *FIELD_METADATA_SEGMENTS,
        object_api_name,
        "fields",
        f"{field_api_name}{FIELD_METADATA_SUFFIX}",
    )
    if not path.is_file():
        raise ContextFeedError("FIELD_METADATA_NOT_FOUND")
    resolved = path.resolve()
    # The API name grammar already excludes separators; this also refuses a symlinked escape.
    if not resolved.is_relative_to(root):
        raise ContextFeedError("FIELD_METADATA_NOT_FOUND")
    element = _field_metadata_root(resolved, field_api_name)
    value_set = _child(element, "valueSet")
    values, truncated = _picklist_values(value_set)
    return FieldMetadata(
        objectApiName=object_api_name,
        fieldApiName=field_api_name,
        type=_child_text(element, "type"),
        label=_child_text(element, "label"),
        required=_child_text(element, "required") == "true",
        referenceTo=_reference_targets(element),
        relationshipName=_child_text(element, "relationshipName"),
        picklistValues=values,
        picklistTruncated=truncated,
        restrictedPicklist=_optional_flag(_child_text(value_set, "restricted")),
        valueSetName=_child_text(value_set, "valueSetName"),
        sourcePath=_relative_posix(root, resolved),
    )


def _repo_root(repo_root: Path) -> Path:
    return Path(repo_root).resolve()


def _knowledge_root(repo_root: Path) -> Path:
    return (_repo_root(repo_root) / KNOWLEDGE_DIRECTORY_NAME).resolve()


def _relative_posix(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _read_document(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _document_headings(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        match = INDEX_HEADING_PATTERN.match(line)
        if match is not None:
            headings.append(match.group(1))
    return headings


def _section_body(text: str, section: str) -> str:
    wanted = section.strip().casefold()
    lines = text.splitlines()
    start: int | None = None
    level = 0
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if match is None:
            continue
        if start is None:
            if match.group(2).strip().casefold() == wanted:
                start = index
                level = len(match.group(1))
            continue
        if len(match.group(1)) <= level:
            return _joined(lines[start:index])
    if start is None:
        raise ContextFeedError("SECTION_NOT_FOUND")
    return _joined(lines[start:])


def _joined(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _load_graph(graph_path: Path) -> dict[str, Any]:
    try:
        raw = Path(graph_path).read_text(encoding="utf-8")
    except OSError as error:
        raise ContextFeedError("GRAPH_NOT_FOUND") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ContextFeedError("GRAPH_INVALID") from error
    if not isinstance(payload, dict):
        raise ContextFeedError("GRAPH_INVALID")
    return payload


def _normalised_edges(raw: Any) -> list[tuple[str, str, str]]:
    if not isinstance(raw, list):
        raise ContextFeedError("GRAPH_INVALID")
    normalised: list[tuple[str, str, str]] = []
    for edge in raw:
        if not isinstance(edge, dict):
            continue
        left = edge.get("from") or edge.get("source")
        right = edge.get("to") or edge.get("target")
        relation = edge.get("type") or edge.get("relation") or DEFAULT_RELATION
        if not isinstance(left, str) or not isinstance(right, str):
            continue
        if not isinstance(relation, str):
            relation = DEFAULT_RELATION
        normalised.append((left, relation, right))
    return normalised


def _field_metadata_root(path: Path, field_api_name: str) -> ElementTree.Element:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ContextFeedError("FIELD_METADATA_UNREADABLE") from error
    if len(content) > MAXIMUM_FIELD_METADATA_BYTES:
        raise ContextFeedError("FIELD_METADATA_TOO_LARGE")
    try:
        element = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ContextFeedError("FIELD_METADATA_UNREADABLE") from error
    if _local_name(element.tag) != "CustomField":
        raise ContextFeedError("FIELD_METADATA_INVALID")
    # A file whose declared fullName disagrees with its own path is not trustworthy identity.
    if _child_text(element, "fullName") != field_api_name:
        raise ContextFeedError("FIELD_METADATA_INVALID")
    return element


def _local_name(tag: str) -> str:
    return tag.rpartition(NAMESPACE_SUFFIX)[2]


def _children(parent: ElementTree.Element | None, name: str) -> list[ElementTree.Element]:
    if parent is None:
        return []
    return [child for child in parent if _local_name(child.tag) == name]


def _child(parent: ElementTree.Element | None, name: str) -> ElementTree.Element | None:
    found = _children(parent, name)
    return found[0] if found else None


def _child_text(parent: ElementTree.Element | None, name: str) -> str | None:
    child = _child(parent, name)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _optional_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _reference_targets(element: ElementTree.Element) -> list[str]:
    targets: list[str] = []
    for child in _children(element, "referenceTo"):
        target = (child.text or "").strip()
        if not API_NAME_PATTERN.fullmatch(target):
            raise ContextFeedError("FIELD_METADATA_INVALID")
        targets.append(target)
    return targets


def _picklist_values(
    value_set: ElementTree.Element | None,
) -> tuple[list[PicklistValue], bool]:
    definition = _child(value_set, "valueSetDefinition")
    entries = _children(definition, "value")
    truncated = len(entries) > MAXIMUM_PICKLIST_VALUES
    values: list[PicklistValue] = []
    for entry in entries[:MAXIMUM_PICKLIST_VALUES]:
        name = _child_text(entry, "fullName")
        if name is None:
            raise ContextFeedError("FIELD_METADATA_INVALID")
        values.append(
            PicklistValue(
                value=name,
                label=_child_text(entry, "label") or name,
                default=_child_text(entry, "default") == "true",
            )
        )
    return values, truncated
