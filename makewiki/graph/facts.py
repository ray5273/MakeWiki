from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from makewiki.errors import GraphError

# CodeFacts holds NON-call structural signals extracted deterministically from a
# static analyzer (Joern CPG). It is deliberately SEPARATE from CodeGraph:
# CodeGraph is the function call/CFG graph that render/mermaid and the wiki
# generator iterate wholesale, so mixing structs/globals into it would pollute
# every call-graph diagram and symbol page. The importance-lens scorers read
# BOTH CodeGraph (topology) and CodeFacts (structural evidence).
#
#   CodeFacts
#     structs   : StructFact   (aggregate types)
#     members   : MemberFact   (struct fields; is_function_pointer => callback)
#     globals   : GlobalFact   (global/shared state)
#     includes  : IncludeFact   (file -> included header, module boundaries)
#     tags      : function_id -> {"entrypoint","alloc","free",...}


@dataclass(frozen=True, order=True)
class StructFact:
    name: str
    file_path: str
    start_line: int

    def validate(self) -> None:
        _require(self.name, "StructFact.name")
        _require(self.file_path, f"StructFact.file_path for {self.name}")
        _require_line(self.start_line, f"StructFact.start_line for {self.name}")


@dataclass(frozen=True, order=True)
class MemberFact:
    struct_name: str
    name: str
    file_path: str
    start_line: int
    is_function_pointer: bool = False

    def validate(self) -> None:
        _require(self.struct_name, "MemberFact.struct_name")
        _require(self.name, f"MemberFact.name in {self.struct_name}")
        _require(self.file_path, f"MemberFact.file_path for {self.struct_name}.{self.name}")
        _require_line(self.start_line, f"MemberFact.start_line for {self.struct_name}.{self.name}")


@dataclass(frozen=True, order=True)
class GlobalFact:
    name: str
    file_path: str
    start_line: int
    type_name: str | None = None

    def validate(self) -> None:
        _require(self.name, "GlobalFact.name")
        _require(self.file_path, f"GlobalFact.file_path for {self.name}")
        _require_line(self.start_line, f"GlobalFact.start_line for {self.name}")


@dataclass(frozen=True, order=True)
class IncludeFact:
    file_path: str
    target: str
    start_line: int

    def validate(self) -> None:
        _require(self.file_path, "IncludeFact.file_path")
        _require(self.target, f"IncludeFact.target in {self.file_path}")
        _require_line(self.start_line, f"IncludeFact.start_line in {self.file_path}")


@dataclass
class CodeFacts:
    repo_root: Path
    structs: list[StructFact] = field(default_factory=list)
    members: list[MemberFact] = field(default_factory=list)
    globals: list[GlobalFact] = field(default_factory=list)
    includes: list[IncludeFact] = field(default_factory=list)
    tags: dict[str, set[str]] = field(default_factory=dict)

    def add_struct(self, struct: StructFact) -> None:
        struct.validate()
        self.structs.append(struct)

    def add_member(self, member: MemberFact) -> None:
        member.validate()
        self.members.append(member)

    def add_global(self, glob: GlobalFact) -> None:
        glob.validate()
        self.globals.append(glob)

    def add_include(self, include: IncludeFact) -> None:
        include.validate()
        self.includes.append(include)

    def tag_function(self, function_id: str, tag: str) -> None:
        _require(function_id, "CodeFacts.tag_function function_id")
        _require(tag, "CodeFacts.tag_function tag")
        self.tags.setdefault(function_id, set()).add(tag)

    def tags_for(self, function_id: str) -> set[str]:
        return set(self.tags.get(function_id, set()))

    def functions_with_tag(self, tag: str) -> list[str]:
        return sorted(fid for fid, tags in self.tags.items() if tag in tags)

    def validate(self) -> None:
        for fact in (*self.structs, *self.members, *self.globals, *self.includes):
            fact.validate()


def _require(value: str, label: str) -> None:
    if not value.strip():
        raise GraphError(f"{label} is required")


def _require_line(line: int, label: str) -> None:
    if line < 1:
        raise GraphError(f"{label} must be >= 1")
