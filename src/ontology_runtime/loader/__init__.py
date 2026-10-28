"""Loader: YAML / JSON -> canonical model.

Pipeline: Parse -> Resolve -> Validate -> freeze.  Core schema never sees
YAML; this module is the only place that knows file formats exist.

v0.2 parses the declarative constraint surface (property constraints,
identity, category, state machine, relation cardinality/inverse, ontology
profile/schema_version).  Illegal values raise :class:`SchemaViolation`
here, at load time, so an invalid schema can never become runtime state.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.errors import SchemaViolation
from ..core.refs import ObjectRef
from ..core.schema import (
    CARDINALITIES,
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
        profile=_parse_profile(onto),
        schema_version=_parse_schema_version(onto),
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
            identity=_parse_identity(name, spec.get("identity"), properties),
            category=_parse_category(name, spec.get("category")),
            state_machine=spec.get("state_machine"),
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
            cardinality=_parse_cardinality(name, spec.get("cardinality")),
            inverse=_parse_inverse(name, spec.get("inverse")),
        )
    _validate_inverses(schema)

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
    """Parse seed data: objects + relations with 'Type/Id' references.

    Objects may carry the (optional) temporal metadata ``version`` /
    ``updated_at`` / ``source``; everything defaults to a fresh object.
    """
    objects: list[Object] = []
    for item in data.get("objects", []) or []:
        ref = ObjectRef(item["type"], item["id"])
        objects.append(
            Object(
                ref=ref,
                properties=dict(item.get("properties", {})),
                version=int(item.get("version", 1)),
                updated_at=item.get("updated_at"),
                source=item.get("source"),
            )
        )
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


def _parse_profile(onto: dict) -> str:
    profile = onto.get("profile", "standard")
    if not isinstance(profile, str) or not profile:
        raise SchemaViolation("ontology.profile must be a non-empty string")
    return profile


def _parse_schema_version(onto: dict) -> int:
    value = onto.get("schema_version", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SchemaViolation("ontology.schema_version must be a positive integer")
    return value


def _parse_category(name: str, raw) -> str:
    if raw is None:
        return "object"
    if not isinstance(raw, str) or not raw:
        raise SchemaViolation(f"object type {name!r}: category must be a string")
    return raw


def _parse_identity(
    name: str, raw, properties: dict[str, PropertyDef]
) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise SchemaViolation(f"object type {name!r}: identity must be a list")
    identity: list[str] = []
    for prop in raw:
        if not isinstance(prop, str) or not prop:
            raise SchemaViolation(
                f"object type {name!r}: identity entries must be property names"
            )
        if prop not in properties:
            raise SchemaViolation(
                f"object type {name!r}: identity property {prop!r} is not defined"
            )
        identity.append(prop)
    if len(set(identity)) != len(identity):
        raise SchemaViolation(f"object type {name!r}: identity has duplicates")
    return tuple(identity)


def _parse_cardinality(name: str, raw) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in CARDINALITIES:
        raise SchemaViolation(
            f"relation {name!r}: invalid cardinality {raw!r}, "
            f"expected one of {list(CARDINALITIES)}"
        )
    return raw


def _parse_inverse(name: str, raw) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise SchemaViolation(f"relation {name!r}: inverse must be a string")
    return raw


def _validate_inverses(schema: OntologySchema) -> None:
    """The inverse role must exist and point the other way (schema coherence)."""
    for relation in schema.relations.values():
        if relation.inverse is None:
            continue
        inverse = schema.relations.get(relation.inverse)
        if inverse is None:
            raise SchemaViolation(
                f"relation {relation.name!r}: inverse {relation.inverse!r} "
                "is not a defined relation"
            )
        if relation.inverse == relation.name and relation.source != relation.target:
            raise SchemaViolation(
                f"relation {relation.name!r}: a non-reflexive relation cannot "
                "be its own inverse"
            )
        if inverse.source != relation.target or inverse.target != relation.source:
            raise SchemaViolation(
                f"relation {relation.name!r}: inverse {inverse.name!r} must go "
                f"{relation.target} -> {relation.source}, "
                f"got {inverse.source} -> {inverse.target}"
            )


def _parse_property(prop_name: str, spec) -> PropertyDef:
    # shorthand: "plate: string" or plate: string
    if isinstance(spec, str):
        spec = {"type": spec}
    if not isinstance(spec, dict):
        raise SchemaViolation(f"property {prop_name!r}: definition must be a mapping")
    type_def = _parse_type(prop_name, "property", spec.get("type"))
    try:
        return PropertyDef(
            type=type_def,
            description=spec.get("description"),
            required=_parse_bool(prop_name, "required", spec.get("required", False)),
            enum=spec.get("enum"),
            minimum=spec.get("minimum"),
            maximum=spec.get("maximum"),
            unique=_parse_bool(prop_name, "unique", spec.get("unique", False)),
            unit=spec.get("unit"),
            precision=spec.get("precision"),
            default=spec.get("default"),
            temporal=_parse_temporal(prop_name, spec.get("temporal", False)),
        )
    except SchemaViolation as exc:
        raise SchemaViolation(f"property {prop_name!r}: {exc}") from None


def _parse_bool(prop_name: str, field: str, value) -> bool:
    if not isinstance(value, bool):
        raise SchemaViolation(
            f"property {prop_name!r}: {field} must be a boolean, got {value!r}"
        )
    return value


def _parse_temporal(prop_name: str, value) -> bool | str:
    """``temporal`` is a flag, or a mode string (e.g. "snapshot", "valid-time")
    when the platform declares a temporal mode."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value:
        return value
    raise SchemaViolation(
        f"property {prop_name!r}: temporal must be a boolean or a non-empty "
        f"mode string, got {value!r}"
    )


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
