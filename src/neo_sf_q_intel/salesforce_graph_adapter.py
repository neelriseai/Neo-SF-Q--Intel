from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from neo_sf_q_intel.graph_adapter import (
    AdapterEdge,
    AdapterFileResult,
    AdapterGraph,
    AdapterNode,
    SemanticFileStatus,
)

_METADATA_NAMESPACE = "http://soap.sforce.com/2006/04/metadata"
_SALESFORCE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_WINDOWS_DEVICE = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


class SalesforceGraphAdapterError(RuntimeError):
    """A Salesforce source artifact could not be classified or parsed deterministically."""


class SalesforceGraphCapacityError(SalesforceGraphAdapterError):
    """Semantic extraction exceeded a configured deterministic work/output limit."""


def _stable(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _elements(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [item for item in root.iter() if _local(item.tag) == name]


def _text(root: ElementTree.Element, name: str) -> str | None:
    for item in root:
        if _local(item.tag) != name:
            continue
        if item.text and item.text.strip():
            return item.text.strip()
    return None


def _texts(root: ElementTree.Element, name: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.text.strip()
                for item in _elements(root, name)
                if item.text and item.text.strip()
            }
        )
    )


def _direct_texts(root: ElementTree.Element, name: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.text.strip()
                for item in root
                if _local(item.tag) == name and item.text and item.text.strip()
            }
        )
    )


def _bool(root: ElementTree.Element, name: str) -> bool:
    value = _text(root, name)
    if value is None:
        return False
    if value.casefold() not in {"true", "false"}:
        raise SalesforceGraphAdapterError(f"Invalid Boolean value for {name}")
    return value.casefold() == "true"


def _text_digest(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    encoded = value.encode("utf-8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "lengthBytes": len(encoded)}


def _xml(content: bytes, path: str, expected_root: str) -> ElementTree.Element:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise SalesforceGraphAdapterError(f"Unsafe XML declaration in {path}")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as exc:
        raise SalesforceGraphAdapterError(f"Malformed Salesforce XML in {path}") from exc
    if root.tag != f"{{{_METADATA_NAMESPACE}}}{expected_root}" or any(
        not item.tag.startswith(f"{{{_METADATA_NAMESPACE}}}") for item in root.iter()
    ):
        raise SalesforceGraphAdapterError(
            f"Unexpected Salesforce metadata schema in {path}"
        )
    stack: list[tuple[ElementTree.Element, int]] = [(root, 1)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 50000 or depth > 128:
            raise SalesforceGraphCapacityError("Salesforce XML structure capacity exceeded")
        stack.extend((child, depth + 1) for child in item)
    return root


def _utf8(content: bytes, path: str) -> str:
    try:
        return content.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise SalesforceGraphAdapterError(f"Non-UTF-8 source in {path}") from exc


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", " ", source)


def _apex_semantic_source(source: str) -> str:
    source = _without_comments(source)
    return re.sub(r"'(?:\\.|[^'\\])*'", "''", source)


def _segment_after(parts: tuple[str, ...], marker: str) -> str | None:
    try:
        index = parts.index(marker)
    except ValueError:
        return None
    return parts[index + 1] if index + 1 < len(parts) else None


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SalesforceGraphAdapterError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _safe_repository_locator(value: str, maximum_path_bytes: int) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
        or len(value.encode("utf-8")) > maximum_path_bytes
        or unicodedata.normalize("NFC", value) != value
    ):
        raise SalesforceGraphAdapterError("Unsafe Salesforce repository locator")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != value
        or any(
            part.endswith((".", " ")) or _WINDOWS_DEVICE.fullmatch(part)
            for part in path.parts
        )
    ):
        raise SalesforceGraphAdapterError("Unsafe Salesforce repository locator")
    return path.as_posix()


def _package_roots(
    files: Mapping[str, bytes], maximum_path_bytes: int
) -> tuple[str, ...]:
    safe_paths = tuple(
        sorted(_safe_repository_locator(path, maximum_path_bytes) for path in files)
    )
    descriptor_aliases = tuple(
        path
        for path in safe_paths
        if PurePosixPath(path).name.casefold() == "sfdx-project.json"
    )
    descriptors = tuple(
        path
        for path in descriptor_aliases
        if PurePosixPath(path).name == "sfdx-project.json"
    )
    if not descriptors:
        raise SalesforceGraphAdapterError("Missing sfdx-project.json")
    if len(descriptors) != 1 or len(descriptor_aliases) != 1:
        raise SalesforceGraphAdapterError("Salesforce project descriptor is ambiguous")
    descriptor = descriptors[0]
    content = files[descriptor]
    try:
        document = json.loads(
            _utf8(content, descriptor), object_pairs_hook=_json_object
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise SalesforceGraphAdapterError("Malformed sfdx-project.json") from exc
    directories = document.get("packageDirectories") if isinstance(document, dict) else None
    if not isinstance(directories, list) or not directories:
        raise SalesforceGraphAdapterError("Missing Salesforce package directories")
    roots: list[str] = []
    for entry in directories:
        root = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(root, str):
            raise SalesforceGraphAdapterError("Unsafe Salesforce package directory")
        _safe_repository_locator(root, maximum_path_bytes)
        parent = PurePosixPath(descriptor).parent
        resolved = PurePosixPath(root) if str(parent) == "." else parent / root
        roots.append(_safe_repository_locator(str(resolved), maximum_path_bytes))
    aliases = [root.casefold() for root in roots]
    if len(aliases) != len(set(aliases)):
        raise SalesforceGraphAdapterError("Duplicate Salesforce package directory")
    ordered = tuple(sorted(roots))
    for index, root in enumerate(ordered):
        if any(
            root.casefold().startswith(f"{other.casefold()}/")
            or other.casefold().startswith(f"{root.casefold()}/")
            for other in ordered[index + 1 :]
        ):
            raise SalesforceGraphAdapterError("Overlapping Salesforce package directory")
    return ordered


@dataclass(slots=True)
class _NodeState:
    raw_kind: str
    label: str
    declaration_owners: set[str] = field(default_factory=set)
    reference_owners: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_state: str = "CONFIRMED"


@dataclass(slots=True)
class _EdgeState:
    source_id: str
    raw_relation: str
    target_id: str
    owners: set[str] = field(default_factory=set)
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_state: str = "CONFIRMED"


class _Builder:
    def __init__(self, maximum_nodes: int, maximum_edges: int, remaining_work: int) -> None:
        self.maximum_nodes = maximum_nodes
        self.maximum_edges = maximum_edges
        self.remaining_work = remaining_work
        self.nodes: dict[str, _NodeState] = {}
        self.edges: dict[str, _EdgeState] = {}
        self.nodes_by_path: dict[str, set[str]] = defaultdict(set)
        self.edges_by_path: dict[str, set[str]] = defaultdict(set)

    def node(
        self,
        node_id: str,
        raw_kind: str,
        label: str,
        path: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        evidence_state: str = "CONFIRMED",
        declared: bool = False,
    ) -> str:
        attrs = dict(attributes or {})
        encoded = json.dumps(attrs, ensure_ascii=False, sort_keys=True).encode()
        cost = len(node_id.encode()) + len(label.encode()) + len(encoded) + 1
        if len(encoded) > 65536 or len(node_id.encode()) > 4600 or len(label.encode()) > 4096:
            raise SalesforceGraphCapacityError("Semantic node output capacity exceeded")
        self.remaining_work -= cost
        if self.remaining_work < 0:
            raise SalesforceGraphCapacityError("Semantic work capacity exceeded")
        current = self.nodes.get(node_id)
        if current is None:
            if len(self.nodes) >= self.maximum_nodes:
                raise SalesforceGraphCapacityError("Semantic node capacity exceeded")
            current = _NodeState(raw_kind, label, evidence_state=evidence_state)
            self.nodes[node_id] = current
        elif current.raw_kind != raw_kind or current.label != label:
            raise SalesforceGraphAdapterError(f"Conflicting node identity: {node_id}")
        elif current.evidence_state == "INFERRED" and evidence_state == "CONFIRMED":
            current.evidence_state = "CONFIRMED"
        for key, value in attrs.items():
            if key in current.attributes and current.attributes[key] != value:
                raise SalesforceGraphAdapterError(f"Conflicting node attribute: {node_id}:{key}")
            current.attributes[key] = value
        if declared:
            if (
                current.declaration_owners
                and path not in current.declaration_owners
                and raw_kind
                not in {"apex-class", "apex-test", "apex-trigger", "lightning-component"}
            ):
                raise SalesforceGraphAdapterError(
                    f"Duplicate semantic declaration: {node_id}"
                )
            current.declaration_owners.add(path)
        else:
            current.reference_owners.add(path)
        if declared:
            self.edge(node_id, "defined_in", f"file:{path}", path)
        return node_id

    def edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        path: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        evidence_state: str = "CONFIRMED",
    ) -> str:
        attrs = dict(attributes or {})
        encoded = json.dumps(attrs, ensure_ascii=False, sort_keys=True).encode()
        cost = sum(
            len(value.encode()) for value in (source_id, relation, target_id)
        ) + len(encoded) + 1
        if len(encoded) > 65536:
            raise SalesforceGraphCapacityError("Semantic edge output capacity exceeded")
        self.remaining_work -= cost
        if self.remaining_work < 0:
            raise SalesforceGraphCapacityError("Semantic work capacity exceeded")
        identity = {
            "source": source_id,
            "relation": relation,
            "target": target_id,
            "attributes": attrs,
            "evidence_state": evidence_state,
        }
        edge_id = f"edge:{_stable(identity)[:32]}"
        current = self.edges.get(edge_id)
        if current is None:
            if len(self.edges) >= self.maximum_edges:
                raise SalesforceGraphCapacityError("Semantic edge capacity exceeded")
            current = _EdgeState(
                source_id,
                relation,
                target_id,
                attributes=attrs,
                evidence_state=evidence_state,
            )
            self.edges[edge_id] = current
        elif (
            current.source_id != source_id
            or current.raw_relation != relation
            or current.target_id != target_id
            or current.attributes != attrs
            or current.evidence_state != evidence_state
        ):
            raise SalesforceGraphAdapterError(f"Conflicting edge identity: {edge_id}")
        current.owners.add(path)
        self.edges_by_path[path].add(edge_id)
        return edge_id

    def graph(self, files: tuple[AdapterFileResult, ...]) -> AdapterGraph:
        nodes = tuple(
            AdapterNode(
                node_id=node_id,
                raw_kind=value.raw_kind,
                label=value.label,
                owner_paths=tuple(
                    sorted(value.declaration_owners or value.reference_owners)
                ),
                attributes={key: value.attributes[key] for key in sorted(value.attributes)},
                evidence_state=value.evidence_state,
            )
            for node_id, value in sorted(self.nodes.items())
        )
        edges = tuple(
            AdapterEdge(
                edge_id=edge_id,
                source_id=value.source_id,
                raw_relation=value.raw_relation,
                target_id=value.target_id,
                owner_paths=tuple(sorted(value.owners)),
                attributes={key: value.attributes[key] for key in sorted(value.attributes)},
                evidence_state=value.evidence_state,
            )
            for edge_id, value in sorted(self.edges.items())
        )
        return AdapterGraph(files=files, nodes=nodes, edges=edges)

    def rebuild_node_ownership(self) -> None:
        self.nodes_by_path.clear()
        for node_id, value in self.nodes.items():
            for path in value.declaration_owners or value.reference_owners:
                self.nodes_by_path[path].add(node_id)


class SalesforceSemanticGraphAdapter:
    """Deterministic, source-owned extraction for common Salesforce DX source families."""

    implementation_id = "salesforce-dx-semantic-graph-adapter"
    implementation_version = "1.0.0"
    __slots__ = ()

    def extract(
        self,
        files: Mapping[str, bytes],
        *,
        maximum_nodes: int,
        maximum_edges: int,
        maximum_work_units: int,
        maximum_path_bytes: int = 1024,
    ) -> AdapterGraph:
        if (
            maximum_nodes < 1
            or maximum_edges < 0
            or maximum_work_units < 1
            or maximum_path_bytes < 32
        ):
            raise SalesforceGraphCapacityError("Semantic extraction limits are invalid")
        work_units = sum(len(content) for content in files.values()) + len(files)
        if work_units > maximum_work_units:
            raise SalesforceGraphCapacityError("Semantic extraction work capacity exceeded")
        builder = _Builder(
            maximum_nodes, maximum_edges, maximum_work_units - work_units
        )
        parser_ids: dict[str, str] = {}
        package_roots = _package_roots(files, maximum_path_bytes)

        def source_path(path: str) -> bool:
            return any(
                path.startswith(f"{root}/main/default/") for root in package_roots
            )

        class_sources: dict[str, str] = {}
        trigger_sources: dict[str, str] = {}
        lwc_bundles: dict[str, str] = {}
        for path in sorted(files):
            if not source_path(path):
                continue
            name = PurePosixPath(path).name
            parts = PurePosixPath(path).parts
            if "/classes/" in f"/{path}" and name.endswith(".cls"):
                logical = name.removesuffix(".cls")
                if logical in class_sources:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate Apex class declaration: {logical}"
                    )
                class_sources[logical] = path
            if "/triggers/" in f"/{path}" and name.endswith(".trigger"):
                logical = name.removesuffix(".trigger")
                if logical in trigger_sources:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate Apex trigger declaration: {logical}"
                    )
                trigger_sources[logical] = path
            if "lwc" in parts:
                logical = _segment_after(parts, "lwc")
                if logical:
                    bundle = "/".join(parts[: parts.index("lwc") + 2])
                    prior = lwc_bundles.setdefault(logical, bundle)
                    if prior != bundle:
                        raise SalesforceGraphAdapterError(
                            f"Duplicate LWC bundle declaration: {logical}"
                        )
        for logical, path in class_sources.items():
            if f"{path}-meta.xml" not in files:
                raise SalesforceGraphAdapterError(
                    f"Apex class metadata is missing: {logical}"
                )
        for logical, path in trigger_sources.items():
            if f"{path}-meta.xml" not in files:
                raise SalesforceGraphAdapterError(
                    f"Apex trigger metadata is missing: {logical}"
                )
        for logical, bundle in lwc_bundles.items():
            if (
                f"{bundle}/{logical}.js-meta.xml" not in files
                or f"{bundle}/{logical}.js" not in files
            ):
                raise SalesforceGraphAdapterError(
                    f"LWC bundle metadata is missing: {logical}"
                )

        class_kinds: dict[str, str] = {}
        trigger_names = set(trigger_sources)
        for path, content in files.items():
            if (
                not source_path(path)
                or "/classes/" not in f"/{path}"
                or not path.endswith(".cls")
            ):
                continue
            source = _apex_semantic_source(_utf8(content, path))
            class_kinds[PurePosixPath(path).name.removesuffix(".cls")] = (
                "apex-test"
                if re.search(r"@isTest\b", source, re.IGNORECASE)
                else "apex-class"
            )
        for path in sorted(files):
            parser_id = self._parse_one(
                path,
                files[path],
                builder,
                class_kinds,
                trigger_names,
                source_path(path),
            )
            parser_ids[path] = parser_id
        builder.rebuild_node_ownership()
        results: list[AdapterFileResult] = []
        for path in sorted(files):
            parser_id = parser_ids[path]
            if parser_id == "salesforce-nonsemantic-artifact":
                status = SemanticFileStatus.NOT_APPLICABLE
                reason = "NO_SEMANTIC_FAMILY"
            elif parser_id == "salesforce-unsupported-source-family":
                status = SemanticFileStatus.UNSUPPORTED
                reason = "UNSUPPORTED_SALESFORCE_METADATA_FAMILY"
            else:
                status = SemanticFileStatus.PARSED
                reason = None
            results.append(
                AdapterFileResult(
                    path=path,
                    status=status,
                    parser_id=parser_id,
                    node_ids=tuple(sorted(builder.nodes_by_path[path])),
                    edge_ids=tuple(sorted(builder.edges_by_path[path])),
                    reason_code=reason,
                )
            )
        return builder.graph(tuple(results))

    def _parse_one(
        self,
        path: str,
        content: bytes,
        builder: _Builder,
        class_kinds: Mapping[str, str],
        trigger_names: set[str],
        in_package_source: bool,
    ) -> str:
        parts = PurePosixPath(path).parts
        name = PurePosixPath(path).name
        if not in_package_source:
            return "salesforce-nonsemantic-artifact"
        if name.endswith(".object-meta.xml") and "objects" in parts:
            self._object(path, content, parts, builder)
            return "salesforce-object-xml"
        if name.endswith(".field-meta.xml") and "fields" in parts and "objects" in parts:
            self._field(path, content, parts, builder)
            return "salesforce-field-xml"
        if name.endswith(".flow-meta.xml") and "flows" in parts:
            self._flow(path, content, builder)
            return "salesforce-flow-xml"
        if name.endswith(".permissionset-meta.xml") and "permissionsets" in parts:
            self._permission_set(path, content, builder)
            return "salesforce-permission-set-xml"
        if name.endswith(".approvalProcess-meta.xml") and "approvalProcesses" in parts:
            self._approval(path, content, builder)
            return "salesforce-approval-process-xml"
        if name.endswith(".flexipage-meta.xml") and "flexipages" in parts:
            self._flexipage(path, content, builder)
            return "salesforce-flexipage-xml"
        if name.endswith(".app-meta.xml") and "applications" in parts:
            self._application(path, content, builder)
            return "salesforce-application-xml"
        if name.endswith(".report-meta.xml") and "reports" in parts:
            self._report(path, content, builder)
            return "salesforce-report-xml"
        if name.endswith(".reportFolder-meta.xml") and "reports" in parts:
            self._report_folder(path, content, builder)
            return "salesforce-report-folder-xml"
        if name.endswith(".tab-meta.xml") and "tabs" in parts:
            self._tab(path, content, builder)
            return "salesforce-custom-tab-xml"
        if name.endswith(".listView-meta.xml") and "listViews" in parts:
            self._list_view(path, content, parts, builder)
            return "salesforce-list-view-xml"
        if name.endswith(".layout-meta.xml") and "layouts" in parts:
            self._layout(path, content, builder)
            return "salesforce-layout-xml"
        if name.endswith(".workflow-meta.xml") and "workflows" in parts:
            self._workflow(path, content, builder)
            return "salesforce-workflow-xml"
        if name.endswith(".md-meta.xml") and "customMetadata" in parts:
            self._custom_metadata(path, content, builder)
            return "salesforce-custom-metadata-xml"
        if name.endswith(".cls") and "classes" in parts:
            self._apex(path, content, builder, class_kinds)
            return "salesforce-apex-lexical"
        if name.endswith(".cls-meta.xml") and "classes" in parts:
            self._apex_meta(path, content, builder, class_kinds)
            return "salesforce-apex-class-xml"
        if name.endswith(".trigger") and "triggers" in parts:
            self._trigger(path, content, builder)
            return "salesforce-trigger-lexical"
        if name.endswith(".trigger-meta.xml") and "triggers" in parts:
            self._trigger_meta(path, content, builder, trigger_names)
            return "salesforce-apex-trigger-xml"
        if "lwc" in parts and name.endswith(".js-meta.xml"):
            self._lwc_meta(path, content, parts, builder)
            return "salesforce-lwc-metadata-xml"
        if "lwc" in parts and name.endswith((".js", ".html", ".css", ".svg")):
            self._lwc(path, content, parts, builder)
            return "salesforce-lwc-source"
        return "salesforce-unsupported-source-family"

    @staticmethod
    def _object(path: str, content: bytes, parts: tuple[str, ...], builder: _Builder) -> None:
        root = _xml(content, path, "CustomObject")
        object_name = _segment_after(parts, "objects")
        if not object_name or not _SALESFORCE_NAME.fullmatch(object_name):
            raise SalesforceGraphAdapterError(f"Object path is incomplete: {path}")
        builder.node(
            f"object:{object_name}",
            "object",
            object_name,
            path,
            {
                "displayLabelDigest": _text_digest(_text(root, "label")),
                "sharingModel": _text(root, "sharingModel"),
                "externalSharingModel": _text(root, "externalSharingModel"),
            },
            declared=True,
        )

    @staticmethod
    def _field(path: str, content: bytes, parts: tuple[str, ...], builder: _Builder) -> None:
        root = _xml(content, path, "CustomField")
        object_name = _segment_after(parts, "objects")
        field_name = _text(root, "fullName")
        path_field_name = PurePosixPath(path).name.removesuffix(".field-meta.xml")
        if (
            not object_name
            or not field_name
            or field_name != path_field_name
            or not _SALESFORCE_NAME.fullmatch(object_name)
            or not _SALESFORCE_NAME.fullmatch(field_name)
        ):
            raise SalesforceGraphAdapterError(f"Field identity is incomplete: {path}")
        object_id = builder.node(f"object:{object_name}", "object", object_name, path)
        full_name = f"{object_name}.{field_name}"
        field_id = builder.node(
            f"field:{full_name}",
            "field",
            full_name,
            path,
            {
                "type": _text(root, "type"),
                "displayLabelDigest": _text_digest(_text(root, "label")),
                "required": _bool(root, "required"),
                "length": _text(root, "length"),
                "precision": _text(root, "precision"),
                "scale": _text(root, "scale"),
            },
            declared=True,
        )
        builder.edge(object_id, "has_field", field_id, path)
        for target in _direct_texts(root, "referenceTo"):
            if not _SALESFORCE_NAME.fullmatch(target):
                raise SalesforceGraphAdapterError(f"Invalid field reference target: {path}")
            target_id = builder.node(f"object:{target}", "object", target, path)
            builder.edge(
                field_id,
                "references",
                target_id,
                path,
                {
                    "relationshipName": _text(root, "relationshipName"),
                    "deleteConstraint": _text(root, "deleteConstraint"),
                },
            )

    @staticmethod
    def _apex(
        path: str,
        content: bytes,
        builder: _Builder,
        class_kinds: Mapping[str, str],
    ) -> None:
        source = _apex_semantic_source(_utf8(content, path))
        name = PurePosixPath(path).name.removesuffix(".cls")
        declaration = re.search(
            r"\b(?:public|global|private|protected)?\s*"
            r"(?:with\s+sharing\s+|without\s+sharing\s+|inherited\s+sharing\s+)?"
            r"(?:abstract\s+|virtual\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            source,
            re.IGNORECASE,
        )
        if declaration is None or declaration.group(1) != name:
            raise SalesforceGraphAdapterError(f"Apex class declaration mismatch: {path}")
        is_test = re.search(r"@isTest\b", source, re.IGNORECASE) is not None
        node_id = builder.node(
            f"apex:{name}",
            "apex-test" if is_test else "apex-class",
            name,
            path,
            {
                "withSharing": bool(re.search(r"\bwith\s+sharing\s+class\b", source)),
                "restResource": "@RestResource" in source,
                "auraEnabled": "@AuraEnabled" in source,
                "invocable": "@InvocableMethod" in source,
            },
            declared=True,
        )
        identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", source))
        for referenced in sorted((set(class_kinds) - {name}) & identifiers):
            if referenced in identifiers:
                if class_kinds[referenced] == "apex-test":
                    continue
                target = builder.node(
                    f"apex:{referenced}", class_kinds[referenced], referenced, path
                )
                relation = "tests" if is_test else "calls"
                builder.edge(
                    node_id,
                    relation,
                    target,
                    path,
                    {"basis": "LEXICAL_SYMBOL_REFERENCE"},
                    evidence_state="INFERRED",
                )
        for query in re.finditer(
            r"\[\s*SELECT\s+(?P<fields>.*?)\s+FROM\s+(?P<object>[A-Za-z_][A-Za-z0-9_]*)",
            source,
            re.IGNORECASE | re.DOTALL,
        ):
            object_name = query.group("object")
            builder.node(
                f"object:{object_name}",
                "object",
                object_name,
                path,
                evidence_state="INFERRED",
            )
            for field_name in sorted(
                {
                    item.strip().split()[0]
                    for item in query.group("fields").split(",")
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", item.strip().split()[0])
                }
            ):
                full_name = field_name if "." in field_name else f"{object_name}.{field_name}"
                field_id = builder.node(
                    f"field:{full_name}",
                    "field",
                    full_name,
                    path,
                    evidence_state="INFERRED",
                )
                builder.edge(
                    node_id,
                    "reads",
                    field_id,
                    path,
                    {"basis": "SOQL_LEXICAL_MATCH"},
                    evidence_state="INFERRED",
                )

    @staticmethod
    def _trigger(path: str, content: bytes, builder: _Builder) -> None:
        source = _apex_semantic_source(_utf8(content, path))
        match = re.search(
            r"\btrigger\s+([A-Za-z_][A-Za-z0-9_]*)\s+on\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]+)\)",
            source,
            re.IGNORECASE,
        )
        if not match:
            raise SalesforceGraphAdapterError(f"Unsupported trigger declaration: {path}")
        trigger_name, object_name, events = match.groups()
        if trigger_name != PurePosixPath(path).name.removesuffix(".trigger"):
            raise SalesforceGraphAdapterError(f"Apex trigger declaration mismatch: {path}")
        trigger_id = builder.node(
            f"trigger:{trigger_name}",
            "apex-trigger",
            trigger_name,
            path,
            {"events": tuple(sorted(item.strip() for item in events.split(",")))},
            declared=True,
        )
        object_id = builder.node(f"object:{object_name}", "object", object_name, path)
        builder.edge(
            object_id, "triggers", trigger_id, path, evidence_state="INFERRED"
        )

    @staticmethod
    def _apex_meta(
        path: str,
        content: bytes,
        builder: _Builder,
        class_kinds: Mapping[str, str],
    ) -> None:
        root = _xml(content, path, "ApexClass")
        name = PurePosixPath(path).name.removesuffix(".cls-meta.xml")
        kind = class_kinds.get(name)
        if kind is None:
            raise SalesforceGraphAdapterError(f"Apex class source is missing: {path}")
        builder.node(
            f"apex:{name}",
            kind,
            name,
            path,
            {"apiVersion": _text(root, "apiVersion"), "status": _text(root, "status")},
            declared=True,
        )

    @staticmethod
    def _trigger_meta(
        path: str,
        content: bytes,
        builder: _Builder,
        trigger_names: set[str],
    ) -> None:
        root = _xml(content, path, "ApexTrigger")
        name = PurePosixPath(path).name.removesuffix(".trigger-meta.xml")
        if name not in trigger_names:
            raise SalesforceGraphAdapterError(f"Apex trigger source is missing: {path}")
        builder.node(
            f"trigger:{name}",
            "apex-trigger",
            name,
            path,
            {"apiVersion": _text(root, "apiVersion"), "status": _text(root, "status")},
            declared=True,
        )

    @staticmethod
    def _lwc_meta(
        path: str, content: bytes, parts: tuple[str, ...], builder: _Builder
    ) -> None:
        root = _xml(content, path, "LightningComponentBundle")
        name = _segment_after(parts, "lwc")
        if not name:
            raise SalesforceGraphAdapterError(f"LWC path is incomplete: {path}")
        builder.node(
            f"lwc:{name}",
            "lightning-component",
            name,
            path,
            {
                "apiVersion": _text(root, "apiVersion"),
                "isExposed": _bool(root, "isExposed"),
                "targets": _texts(root, "target"),
            },
            declared=True,
        )

    @staticmethod
    def _flow(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "Flow")
        name = PurePosixPath(path).name.removesuffix(".flow-meta.xml")
        flow_id = builder.node(
            f"flow:{name}",
            "flow",
            name,
            path,
            {
                "status": _text(root, "status"),
                "processType": _text(root, "processType"),
                "triggerType": _text(root, "triggerType"),
            },
            declared=True,
        )
        object_name = _text(root, "object")
        if object_name:
            object_id = builder.node(f"object:{object_name}", "object", object_name, path)
            builder.edge(object_id, "triggers", flow_id, path)
        for filter_element in _elements(root, "filters"):
            field_name = _text(filter_element, "field")
            if field_name and object_name:
                full_name = field_name if "." in field_name else f"{object_name}.{field_name}"
                field_id = builder.node(f"field:{full_name}", "field", full_name, path)
                builder.edge(
                    flow_id, "reads", field_id, path, evidence_state="INFERRED"
                )
        for action in _elements(root, "actionCalls"):
            if (_text(action, "actionType") or "").casefold() == "apex":
                action_name = _text(action, "actionName")
                if action_name:
                    apex_id = builder.node(f"apex:{action_name}", "apex-class", action_name, path)
                    builder.edge(flow_id, "calls", apex_id, path)

    @staticmethod
    def _permission_set(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "PermissionSet")
        name = PurePosixPath(path).name.removesuffix(".permissionset-meta.xml")
        permission_id = builder.node(
            f"permission-set:{name}",
            "permission-set",
            name,
            path,
            {"grantsAreAdditive": True},
            declared=True,
        )
        seen_objects: set[str] = set()
        for block in _elements(root, "objectPermissions"):
            object_name = _text(block, "object")
            if object_name:
                if object_name in seen_objects:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate object permission: {path}:{object_name}"
                    )
                seen_objects.add(object_name)
                grants = {
                    key: _bool(block, key)
                    for key in (
                        "allowCreate",
                        "allowRead",
                        "allowEdit",
                        "allowDelete",
                        "viewAllRecords",
                        "modifyAllRecords",
                    )
                }
                if any(grants.values()):
                    object_id = builder.node(
                        f"object:{object_name}", "object", object_name, path
                    )
                    builder.edge(
                        permission_id,
                        "object_grant",
                        object_id,
                        path,
                        grants,
                    )
        seen_fields: set[str] = set()
        for block in _elements(root, "fieldPermissions"):
            full_name = _text(block, "field")
            if full_name:
                if full_name in seen_fields:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate field permission: {path}:{full_name}"
                    )
                seen_fields.add(full_name)
                grants = {
                    "readable": _bool(block, "readable"),
                    "editable": _bool(block, "editable"),
                }
                if any(grants.values()):
                    field_id = builder.node(
                        f"field:{full_name}", "field", full_name, path
                    )
                    builder.edge(
                        permission_id,
                        "field_grant",
                        field_id,
                        path,
                        grants,
                    )
        seen_classes: set[str] = set()
        for block in _elements(root, "classAccesses"):
            class_name = _text(block, "apexClass")
            if class_name:
                if class_name in seen_classes:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate class access: {path}:{class_name}"
                    )
                seen_classes.add(class_name)
            if class_name and _bool(block, "enabled"):
                class_id = builder.node(f"apex:{class_name}", "apex-class", class_name, path)
                builder.edge(
                    permission_id,
                    "class_access",
                    class_id,
                    path,
                    {"enabled": True},
                )

    @staticmethod
    def _lwc(
        path: str, content: bytes, parts: tuple[str, ...], builder: _Builder
    ) -> None:
        source = _utf8(content, path)
        name = _segment_after(parts, "lwc")
        if not name:
            raise SalesforceGraphAdapterError(f"LWC path is incomplete: {path}")
        if "/__tests__/" in f"/{path}" and path.endswith(".test.js"):
            test_id = builder.node(
                f"component-test:{path}",
                "component-test",
                PurePosixPath(path).name,
                path,
                declared=True,
            )
            component_id = builder.node(f"lwc:{name}", "lightning-component", name, path)
            builder.edge(test_id, "tests", component_id, path)
            return
        component_id = builder.node(
            f"lwc:{name}", "lightning-component", name, path, declared=True
        )
        if path.endswith(".js"):
            for match in re.finditer(
                r"(?m)^\s*import\s+[^\r\n;]+\s+from\s+['\"]@salesforce/apex/"
                r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)['\"]",
                _without_comments(source),
            ):
                class_name, method_name = match.groups()
                class_id = builder.node(f"apex:{class_name}", "apex-class", class_name, path)
                builder.edge(
                    component_id,
                    "calls_internal_apex",
                    class_id,
                    path,
                    {"method": method_name},
                    evidence_state="INFERRED",
                )
        if path.endswith(".html"):
            source = re.sub(r"<!--.*?-->", " ", source, flags=re.DOTALL)
            for match in re.finditer(r"<c-([a-z][a-z0-9-]*)[\s>]", source):
                child = re.sub(r"-([a-z0-9])", lambda item: item.group(1).upper(), match.group(1))
                child_id = builder.node(f"lwc:{child}", "lightning-component", child, path)
                builder.edge(
                    component_id,
                    "contains",
                    child_id,
                    path,
                    evidence_state="INFERRED",
                )

    @staticmethod
    def _approval(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "ApprovalProcess")
        name = PurePosixPath(path).name.removesuffix(".approvalProcess-meta.xml")
        builder.node(
            f"approval:{name}",
            "approval-process",
            name,
            path,
            {
                "active": _bool(root, "active"),
                "recordEditability": _text(root, "recordEditability"),
            },
            declared=True,
        )

    @staticmethod
    def _flexipage(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "FlexiPage")
        name = PurePosixPath(path).name.removesuffix(".flexipage-meta.xml")
        page_id = builder.node(f"page:{name}", "lightning-page", name, path, declared=True)
        for component in _texts(root, "componentName"):
            if component.startswith("c:"):
                child = component[2:]
                child_id = builder.node(f"lwc:{child}", "lightning-component", child, path)
                builder.edge(page_id, "contains", child_id, path)

    @staticmethod
    def _application(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "CustomApplication")
        name = PurePosixPath(path).name.removesuffix(".app-meta.xml")
        builder.node(
            f"app:{name}",
            "lightning-app",
            name,
            path,
            {
                "displayLabelDigest": _text_digest(_text(root, "label")),
                "tabs": _texts(root, "tabs"),
            },
            declared=True,
        )

    @staticmethod
    def _report(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "Report")
        name = PurePosixPath(path).name.removesuffix(".report-meta.xml")
        builder.node(
            f"report:{path}",
            "saved-report",
            name,
            path,
            {
                "displayNameDigest": _text_digest(_text(root, "name")),
                "format": _text(root, "format"),
                "reportType": _text(root, "reportType"),
            },
            declared=True,
        )

    @staticmethod
    def _report_folder(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "ReportFolder")
        name = PurePosixPath(path).name.removesuffix(".reportFolder-meta.xml")
        builder.node(
            f"report-folder:{name}",
            "report-folder",
            name,
            path,
            {"displayNameDigest": _text_digest(_text(root, "name"))},
            declared=True,
        )

    @staticmethod
    def _tab(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "CustomTab")
        name = PurePosixPath(path).name.removesuffix(".tab-meta.xml")
        builder.node(
            f"tab:{name}",
            "custom-tab",
            name,
            path,
            {
                "labelDigest": _text_digest(_text(root, "label")),
                "lwcComponent": _text(root, "lwcComponent"),
            },
            declared=True,
        )

    @staticmethod
    def _list_view(
        path: str, content: bytes, parts: tuple[str, ...], builder: _Builder
    ) -> None:
        root = _xml(content, path, "ListView")
        object_name = _segment_after(parts, "objects")
        name = PurePosixPath(path).name.removesuffix(".listView-meta.xml")
        builder.node(
            f"list-view:{object_name or 'unknown'}:{name}",
            "list-view",
            name,
            path,
            {
                "displayLabelDigest": _text_digest(_text(root, "label")),
                "object": object_name,
                "columns": _texts(root, "columns"),
            },
            declared=True,
        )

    @staticmethod
    def _layout(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "Layout")
        name = PurePosixPath(path).name.removesuffix(".layout-meta.xml")
        builder.node(
            f"layout:{name}",
            "presentation-configuration",
            name,
            path,
            {"layoutSections": len(_elements(root, "layoutSections"))},
            declared=True,
        )

    @staticmethod
    def _workflow(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "Workflow")
        object_name = PurePosixPath(path).name.removesuffix(".workflow-meta.xml")
        for update in _elements(root, "fieldUpdates"):
            name = _text(update, "fullName")
            field_name = _text(update, "field")
            if not name or not field_name:
                raise SalesforceGraphAdapterError(f"Workflow update identity is incomplete: {path}")
            update_id = builder.node(
                f"field-update:{object_name}.{name}",
                "workflow-field-update",
                name,
                path,
                {"literalValueDigest": _text_digest(_text(update, "literalValue"))},
                declared=True,
            )
            full_name = f"{object_name}.{field_name}"
            field_id = builder.node(f"field:{full_name}", "field", full_name, path)
            builder.edge(update_id, "writes", field_id, path)

    @staticmethod
    def _custom_metadata(path: str, content: bytes, builder: _Builder) -> None:
        root = _xml(content, path, "CustomMetadata")
        name = PurePosixPath(path).name.removesuffix(".md-meta.xml")
        values: dict[str, dict[str, Any] | None] = {}
        for block in _elements(root, "values"):
            field_name = _text(block, "field")
            if field_name:
                if field_name in values:
                    raise SalesforceGraphAdapterError(
                        f"Duplicate custom metadata value: {path}:{field_name}"
                    )
                values[field_name] = _text_digest(_text(block, "value"))
        builder.node(
            f"config:{name}",
            "custom-metadata-record",
            name,
            path,
            {"values": {key: values[key] for key in sorted(values)}},
            declared=True,
        )
