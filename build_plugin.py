#!/usr/bin/env python
"""Validate then package the odoo-assistant plugin.

Usage:  python build_plugin.py [--skip-tests]

Gate order (any failure aborts — nothing is packaged):
  1. Manifest + SKILL.md hygiene — same checks the PostToolUse hook runs
     (hooks/skill_plugin_hygiene.py is imported, not duplicated): plugin.json valid /
     kebab name / description <= 500; every SKILL.md LF-only, frontmatter well-formed,
     name+description only, description <= 1024.
  2. Config files parse: .mcp.json (seeded from .mcp.json.example when absent — point it at
     your own instance), hooks/hooks.json (and the hook script it points at exists).
  3. Playbook cross-checks: every playbooks/*.md is linked from SKILL.md and every
     playbooks/ link in SKILL.md resolves to a file — in BOTH copies.
  4. Source/plugin drift: odin/playbooks and odin/references must be byte-identical to the
     plugin's copies (SKILL.md is ALLOWED to differ — the plugin one carries an extra
     OAuth-connector note — but its playbook table must match, which check 3 enforces).
  5. Server policy tests: python -m pytest odoo-mcp/tests (skippable with --skip-tests;
     the suite pins the unlink block + protected-model blocklist the plugin relies on).

Then builds:  odoo-assistant.plugin  (zip of odoo-assistant-plugin/ contents at zip root)
              odin.zip               (the standalone odin/ skill)
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_DIR = ROOT / "odoo-assistant-plugin"
SKILL_DIRS = [ROOT / "odin", PLUGIN_DIR / "skills" / "odin"]

failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def _load_hygiene():
    spec = importlib.util.spec_from_file_location(
        "skill_plugin_hygiene", PLUGIN_DIR / "hooks" / "skill_plugin_hygiene.py")
    mod = importlib.util.module_from_spec(spec)
    # The hook body reads stdin at import time; execute only up to the function defs by
    # feeding it an empty stdin via a guarded exec of its source's def-blocks instead.
    src = (PLUGIN_DIR / "hooks" / "skill_plugin_hygiene.py").read_text(encoding="utf-8")
    guarded = src.split("\ntry:\n    payload = json.load(sys.stdin)")[0]
    ns: dict = {}
    exec(compile(guarded, str(PLUGIN_DIR / "hooks" / "skill_plugin_hygiene.py"), "exec"), ns)
    return ns["check_skill_md"], ns["check_plugin_json"]


def check_hygiene() -> None:
    print("[1/5] Manifest + SKILL.md hygiene")
    check_skill_md, check_plugin_json = _load_hygiene()

    manifest = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
    manifest_issues = check_plugin_json(manifest.read_text(encoding="utf-8"))
    for issue in manifest_issues:
        fail(f"{manifest.name}: {issue}")
    if not manifest_issues:
        ok(f"{manifest.relative_to(ROOT)}")

    for sd in SKILL_DIRS:
        p = sd / "SKILL.md"
        raw = p.read_bytes()
        issues = check_skill_md(raw.decode("utf-8", errors="ignore"))
        if b"\r\n" in raw:
            issues.append("CRLF line endings — must be LF")
        for issue in issues:
            fail(f"{p.relative_to(ROOT)}: {issue}")
        if not issues:
            ok(f"{p.relative_to(ROOT)}")


def seed_local_mcp_config() -> None:
    """Create .mcp.json from the tracked example on a fresh clone.

    The connector URL is instance-specific, so .mcp.json is gitignored and only
    .mcp.json.example is tracked. Seeding it here keeps `build_plugin.py` working
    straight after a clone — but the placeholder connects nowhere, so say so.
    """
    live = PLUGIN_DIR / ".mcp.json"
    example = PLUGIN_DIR / ".mcp.json.example"
    if live.exists() or not example.exists():
        return
    live.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"  seeded {live.relative_to(ROOT)} from the example - "
          f"edit it to point at your own Odoo instance before using the plugin")


def check_configs() -> None:
    print("[2/5] Config files")
    seed_local_mcp_config()
    for rel in (".mcp.json", "hooks/hooks.json", ".claude-plugin/plugin.json"):
        p = PLUGIN_DIR / rel
        if not p.exists():
            fail(f"missing {p.relative_to(ROOT)}")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
            ok(f"{p.relative_to(ROOT)} parses")
        except json.JSONDecodeError as e:
            fail(f"{p.relative_to(ROOT)}: invalid JSON — {e}")
    hook_py = PLUGIN_DIR / "hooks" / "skill_plugin_hygiene.py"
    if hook_py.exists():
        ok("hooks/skill_plugin_hygiene.py present")
    else:
        fail("hooks/hooks.json points at hooks/skill_plugin_hygiene.py but it is missing")


def check_playbook_links() -> None:
    print("[3/5] Playbook cross-checks")
    for sd in SKILL_DIRS:
        skill = (sd / "SKILL.md").read_text(encoding="utf-8")
        linked = set(re.findall(r"\(playbooks/([a-z0-9-]+\.md)\)", skill))
        on_disk = {p.name for p in (sd / "playbooks").glob("*.md")}
        for missing in sorted(linked - on_disk):
            fail(f"{sd.name}/SKILL.md links playbooks/{missing} but the file does not exist")
        for orphan in sorted(on_disk - linked):
            fail(f"{sd.name}/playbooks/{orphan} exists but is not linked from SKILL.md")
        if linked == on_disk:
            ok(f"{sd.relative_to(ROOT)}: {len(on_disk)} playbooks, table and files agree")


def check_drift() -> None:
    print("[4/5] Source vs plugin drift (playbooks + references)")
    clean = True
    for sub in ("playbooks", "references"):
        src_dir, dst_dir = ROOT / "odin" / sub, PLUGIN_DIR / "skills" / "odin" / sub
        src = {p.name: p.read_bytes() for p in src_dir.glob("*.md")}
        dst = {p.name: p.read_bytes() for p in dst_dir.glob("*.md")}
        for name in sorted(set(src) | set(dst)):
            if name not in dst:
                fail(f"{sub}/{name} missing from the plugin copy"); clean = False
            elif name not in src:
                fail(f"{sub}/{name} only in the plugin copy (missing from odin/)"); clean = False
            elif src[name] != dst[name]:
                fail(f"{sub}/{name} differs between odin/ and the plugin copy — re-sync"); clean = False
    if clean:
        ok("odin/ and plugin copies are identical (SKILL.md exempt by design)")


def run_tests(skip: bool) -> None:
    print("[5/5] Server policy tests")
    if skip:
        notes.append("server tests SKIPPED (--skip-tests)")
        print("  skip  --skip-tests given")
        return
    r = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "odoo-mcp" / "tests"),
                        "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=str(ROOT / "odoo-mcp"))
    tail = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode == 0:
        ok(tail[-1] if tail else "pytest passed")
    elif "No module named pytest" in (r.stdout + r.stderr):
        fail("pytest is not installed (pip install pytest) — or re-run with --skip-tests")
    else:
        fail("server tests failed:\n        " + "\n        ".join(tail[-8:]))


def build() -> None:
    def zipdir(zpath: Path, entries):
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for src, arc in entries:
                if src.is_dir():
                    for p in sorted(src.rglob("*")):
                        if p.is_file() and not {"__pycache__", "evals"} & set(p.parts):
                            z.write(p, arc + "/" + p.relative_to(src).as_posix())
                else:
                    z.write(src, arc)

    zipdir(ROOT / "odoo-assistant.plugin", [
        (PLUGIN_DIR / ".claude-plugin", ".claude-plugin"),
        (PLUGIN_DIR / "hooks", "hooks"),
        (PLUGIN_DIR / "skills", "skills"),
        (PLUGIN_DIR / ".mcp.json", ".mcp.json"),
        (PLUGIN_DIR / "README.md", "README.md"),
    ])
    zipdir(ROOT / "odin.zip", [(ROOT / "odin", "odin")])
    with zipfile.ZipFile(ROOT / "odoo-assistant.plugin") as z:
        mf = json.loads(z.read(".claude-plugin/plugin.json"))
    print(f"\nPackaged odoo-assistant.plugin v{mf['version']} "
          f"({len(zipfile.ZipFile(ROOT / 'odoo-assistant.plugin').namelist())} entries) + odin.zip")


def main() -> int:
    skip_tests = "--skip-tests" in sys.argv
    os.chdir(ROOT)
    check_hygiene()
    check_configs()
    check_playbook_links()
    check_drift()
    run_tests(skip_tests)
    for n in notes:
        print(f"  note  {n}")
    if failures:
        print(f"\nNOT packaged — {len(failures)} check(s) failed.")
        return 1
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
