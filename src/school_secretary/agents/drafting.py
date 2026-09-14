from __future__ import annotations

import re
from pathlib import Path

from school_secretary.agents.llm import INTEGRITY_RULE
from school_secretary.config import Settings, get_settings
from school_secretary.db.models import Assignment, Document
from school_secretary.ingest.parser import extract_word_count

INTEGRITY_HEADER = (
    "ACADEMIC INTEGRITY: Scaffolding only. Outlines, # TODO comments, and test shells. "
    "Do not submit generated work. Implement or write everything yourself."
)

RUBRIC_LINE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z /-]{2,40}).{0,20}?(?P<pct>\d{1,3})\s*%",
    re.IGNORECASE,
)


def parse_rubric_weights(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for match in RUBRIC_LINE.finditer(text):
        pct = int(match.group("pct"))
        if 0 < pct <= 100:
            found.append((match.group("label").strip(" :-"), pct))
    # de-dupe labels, keep first
    seen: set[str] = set()
    unique: list[tuple[str, int]] = []
    for label, pct in found:
        key = label.lower()
        if key not in seen:
            seen.add(key)
            unique.append((label, pct))
    return unique[:8]


def _assignment_text(assignment: Assignment) -> str:
    parts = [assignment.title, assignment.instructions]
    for document in assignment.documents:
        parts.append(document.extracted_text)
    return "\n".join(parts)


def _slug(assignment: Assignment) -> str:
    code = assignment.course.code.lower().replace(" ", "")
    title = re.sub(r"[^a-z0-9]+", "-", assignment.title.lower()).strip("-")[:40]
    return f"{code}-{title}"


def scaffold_assignment(assignment: Assignment, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    settings.ensure_dirs()
    dest = settings.scaffolds_dir / _slug(assignment)
    dest.mkdir(parents=True, exist_ok=True)
    if assignment.assignment_type == "essay":
        _write_essay_outline(dest, assignment)
    elif assignment.assignment_type == "coding_lab":
        _write_coding_scaffold(dest, assignment)
    else:
        _write_generic_outline(dest, assignment)
    return dest


def _write_essay_outline(dest: Path, assignment: Assignment) -> None:
    text = _assignment_text(assignment)
    words = extract_word_count(text) or 1500
    weights = parse_rubric_weights(text) or [
        ("Thesis", 20),
        ("Argument", 40),
        ("Sources", 20),
        ("Style", 20),
    ]
    sections = []
    remaining = words
    for index, (label, pct) in enumerate(weights):
        allot = round(words * pct / 100)
        if index == len(weights) - 1:
            allot = remaining
        remaining -= allot
        sections.append((label, pct, max(allot, 50)))
    lines = [
        f"# Outline — {assignment.course.code}: {assignment.title}",
        "",
        INTEGRITY_HEADER,
        "",
        f"Target length: **{words} words** (from the prompt/rubric).",
        "Fill each section yourself. This file must not become the submitted essay.",
        "",
    ]
    for label, pct, allot in sections:
        lines.extend(
            [
                f"## {label} (~{allot} words, {pct}% of rubric)",
                "",
                f"- TODO: write your own notes for {label.lower()} ({allot} words).",
                "- TODO: add citations you actually read.",
                "",
            ]
        )
    lines.extend(
        [
            "## Closing checklist",
            "",
            "- [ ] Thesis is specific and contestable",
            "- [ ] Every claim has a source or argument",
            "- [ ] Word count within the assigned range",
            "",
        ]
    )
    (dest / "OUTLINE.md").write_text("\n".join(lines), encoding="utf-8")
    (dest / "README.md").write_text(
        f"# {assignment.title}\n\n{INTEGRITY_HEADER}\n\nSee OUTLINE.md. Do not ask a model to write the essay.\n",
        encoding="utf-8",
    )


def _write_coding_scaffold(dest: Path, assignment: Assignment) -> None:
    module = "solution"
    class_name = "LabSolution"
    title = assignment.title.lower()
    if "binary search" in title or "bst" in title:
        module = "bst"
        class_name = "BinarySearchTree"
        methods = [
            ("insert", "key", "insert key into the tree, preserving BST order"),
            ("search", "key", "return True if key is present"),
            ("delete", "key", "delete key and keep BST invariants"),
        ]
    elif "dynamic" in title or "dp" in title:
        module = "dp"
        class_name = "DPAssignment"
        methods = [
            ("solve", "problem", "implement the recurrence yourself from the spec"),
            ("reconstruct", "problem", "optional: reconstruct a chosen solution"),
        ]
    else:
        methods = [
            ("run", "args", "implement the behaviour required by the lab PDF"),
        ]
    integrity = f'"""{INTEGRITY_HEADER}\n\n{assignment.course.code}: {assignment.title}\n"""\n'
    method_blocks = []
    for name, arg, hint in methods:
        method_blocks.append(
            f"    def {name}(self, {arg}):\n"
            f"        # TODO: {hint}\n"
            f"        raise NotImplementedError({hint!r})\n"
        )
    (dest / f"{module}.py").write_text(
        integrity
        + "\n"
        + f"class {class_name}:\n"
        + "    def __init__(self):\n"
        + "        # TODO: initialize only the fields the spec requires\n"
        + "        pass\n\n"
        + "\n".join(method_blocks),
        encoding="utf-8",
    )
    test_lines = [
        '"""Test shells. Fill in assertions after you implement the module. Do not copy solutions."""',
        "",
        "import pytest",
        "",
        f"from {module} import {class_name}",
        "",
    ]
    for name, _arg, _hint in methods:
        test_lines.extend(
            [
                f"@pytest.mark.skip(reason='Implement {class_name}.{name} yourself first')",
                f"def test_{name}_shell():",
                f"    work = {class_name}()",
                f"    # TODO: call work.{name}(...) with your own examples from the lab PDF",
                "    assert False, 'TODO: replace with real assertions you wrote'",
                "",
            ]
        )
    (dest / f"test_{module}.py").write_text("\n".join(test_lines) + "\n", encoding="utf-8")
    (dest / "README.md").write_text(
        "\n".join(
            [
                f"# {assignment.course.code} — {assignment.title}",
                "",
                INTEGRITY_HEADER,
                "",
                "This folder is GitHub-ready scaffolding:",
                f"- `{module}.py` — class/method stubs with `# TODO` and `NotImplementedError`",
                f"- `test_{module}.py` — skipped unit-test shells",
                "",
                "Implement the lab yourself. Do not paste model-generated algorithms here.",
                "",
                "Suggested git start:",
                "```",
                "git init",
                f"git add {module}.py test_{module}.py README.md",
                'git commit -m "Lab scaffold (stubs only)"',
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_generic_outline(dest: Path, assignment: Assignment) -> None:
    (dest / "OUTLINE.md").write_text(
        "\n".join(
            [
                f"# {assignment.course.code}: {assignment.title}",
                "",
                INTEGRITY_HEADER,
                "",
                "## TODO",
                "- Read the instructions in onQ",
                "- List deliverables",
                "- Schedule work using `school-secretary plan`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def describe_scaffold(path: Path) -> str:
    files = sorted(p.name for p in path.iterdir() if p.is_file())
    return f"Wrote scaffold to {path} ({', '.join(files)}). {INTEGRITY_RULE}"
