"""Loader: YAML / JSON -> canonical model.

Pipeline: Parse -> Resolve -> Validate -> freeze.  Core schema never sees
YAML; this module is the only place that knows file formats exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.errors import SchemaViolation
from ..core.refs import ObjectRef
from ..core.schema import (
    CapabilityCatalog,
    FunctionDef,
    ObjectType,
    OntologySchema,
    PropertyDef,
    RelationDef,
)
from ..core.state import Object, Relation
from ..core.types import TypeDef
from ..infrastructure.bindings import FunctionBinding, load_bindings


def _load_yaml_module():
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyYAML is required to load YAML files. "
            "Install with: pip install ontology-runtime[yaml]"
        ) from exc
    return yaml


def load_dict(path: str | Path) -> dict:
    """Load a YAML or JSON file into a plain dict."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        yaml = _load_yaml_module()
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text or "{}")
    if not isinstance(data, dict):
        raise SchemaViolation(f"{path} must contain a mapping at top level")
    return data


# ----------------------------------------------------------------------
# schema + capability catalog
# ----------------------------------------------------------------------


def parse_schema(data: dict) -> tuple[OntologySchema, CapabilityCatalog]:
    """Build and validate (schema, catalog) from a canonical dict."""
    onto = data.get("ontology", {}) or {}
    schema = OntologySchema(
        name=str(onto.get("name", "untitled")),
        version=int(onto.get("version", 1)),
        namespace=onto.get("namespace"),
    )

    objects_data = data.get("objects", {}) or {}
    for name, spec in objects_data.items():
        spec = spec or {}
        properties: dict[str, PropertyDef] = {}
        for prop_name, prop_spec in (spec.get("properties", {}) or {}).items():
            properties[prop_name] = _parse_property(prop_name, prop_spec)
        schema.objects[name] = ObjectType(
            name=name,
            description=spec.get("description"),
            properties=properties,
        )

    relations_data = data.get("relations", {}) or {}
    for name, spec in relations_data.items():
        spec = spec or {}
        source, target = spec.get("source"), spec.get("target")
        if source not in schema.objects:
            raise SchemaViolation(
                f"relation {name!r}: source {source!r} is not a defined object type"
            )
        if target not in schema.objects:
            raise SchemaViolation(
                f"relation {name!r}: target {target!r} is not a defined object type"
            )
        schema.relations[name] = RelationDef(
            name=name,
            source=source,
            target=target,
            cardinality=spec.get("cardinality"),
        )

    functions_data = data.get("functions", {}) or {}
    catalog = CapabilityCatalog()
    for name, spec in functions_data.items():
        spec = spec or {}
        target_type = spec.get("target")
        if target_type is not None and target_type not in schema.objects:
            raise SchemaViolation(
                f"function {name!r}: target {target_type!r} is not a defined object type"
            )
        if target_type is not None and not name.startswith(f"{target_type}."):
            raise SchemaViolation(
                f"function {name!r} targets {target_type!r}; "
                f"name should be '{target_type}.<capability>'"
            )
        inputs = {
            arg_name: _parse_type(arg_name, name, arg_spec)
            for arg_name, arg_spec in (spec.get("input", {}) or {}).items()
        }
        catalog.functions[name] = FunctionDef(
            name=name,
            target_type=target_type,
            description=str(spec.get("description", "")),
            inputs=inputs,
        )
    return schema, catalog


def load_schema(path: str | Path) -> tuple[OntologySchema, CapabilityCatalog]:
    """Load ontology + functions from one YAML/JSON file."""
    return parse_schema(load_dict(path))


def load_bindings_file(path: str | Path) -> dict[str, FunctionBinding]:
    path = Path(path)
    return load_bindings(load_dict(path), base_dir=path.parent.resolve())


# ----------------------------------------------------------------------
# seed data
# ----------------------------------------------------------------------


def parse_seed(data: dict) -> tuple[list[Object], list[Relation]]:
    """Parse seed data: objects + relations with 'Type/Id' references."""
    objects: list[Object] = []
    for item in data.get("objects", []) or []:
        ref = ObjectRef(item["type"], item["id"])
        objects.append(Object(ref=ref, properties=dict(item.get("properties", {}))))
    relations: list[Relation] = []
    for item in data.get("relations", []) or []:
        relations.append(
            Relation(
                source=ObjectRef.parse(item["source"]),
                relation=item["relation"],
                target=ObjectRef.parse(item["target"]),
            )
        )
    return objects, relations


def load_seed(path: str | Path) -> tuple[list[Object], list[Relation]]:
    return parse_seed(load_dict(path))


# ----------------------------------------------------------------------
# small parsers
# ----------------------------------------------------------------------


def _parse_property(prop_name: str, spec) -> PropertyDef:
    # shorthand: "plate: string" or plate: string
    if isinstance(spec, str):
        spec = {"type": spec}
    type_def = _parse_type(prop_name, "property", spec.get("type"))
    return PropertyDef(type=type_def, description=spec.get("description"))


def _parse_type(what: str, owner: str, spec) -> TypeDef:
    if isinstance(spec, str):
        kind = spec
    elif isinstance(spec, dict):
        kind = spec.get("type")
    else:
        kind = None
    if not kind:
        raise SchemaViolation(f"{owner}: {what!r} is missing a type")
    try:
        return TypeDef(kind=str(kind))
    except SchemaViolation as exc:
        raise SchemaViolation(f"{owner}: {what!r} -> {exc}") from None
