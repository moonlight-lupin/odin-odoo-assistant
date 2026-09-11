#!/usr/bin/env python
"""Skill/plugin hygiene — a PostToolUse hook on Write/Edit (modelled on qip-core's
skillmd_hygiene.py). After a SKILL.md or .claude-plugin/plugin.json is written, it checks the
recurring breakages and WARNS (non-blocking) so they're fixed before the skill/plugin is relied on:

SKILL.md:
  1. LF line endings (CRLF breaks the skills-viewer frontmatter parse)
  2. `---` must be line 1 and the frontmatter must close
  3. frontmatter = name + description only (extra keys break the viewer's parse)
  4. name present, lowercase-kebab
  5. description present and **at most 1024 characters** (the plugin loader hard-rejects longer:
     "field 'description' in SKILL.md must be at most 1024 characters")

plugin.json (.claude-plugin/plugin.json):
  1. valid JSON
  2. name present, lowercase-kebab; description present and **at most 500 characters**
     (the plugin loader hard-rejects longer: "Plugin description must be at most 500 characters")

It WARNS, it doesn't modify the file or block — the model/maintainer fixes it. Silent for any
other file. Reads the tool call on stdin; exit 0 always.
"""
import json
import re
import sys

SKILL_DESC_LIMIT = 1024
PLUGIN_DESC_LIMIT = 500


def fold_description(fm_lines, start_idx, indicator):
    """Fold a YAML block scalar (>' or '|') the way the loader measures it: strip each line,
    join with spaces; for a plain single-line value just return it."""
    parts = []
    for line in fm_lines[start_idx:]:
        if line.strip() == "":
            continue
        if not line.startswith((" ", "\t")):  # next top-level key
            break
        parts.append(line.strip())
    return " ".join(parts)


def check_skill_md(txt):
    issues = []
    if not txt.startswith("---\n"):
        issues.append("first line must be '---' (YAML frontmatter opener)")
        return issues
    lines = txt.splitlines()
    close = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if close is None:
        issues.append("frontmatter closing '---' not found")
        return issues
    fm_lines = lines[1:close]
    fm = "\n".join(fm_lines)

    keys = re.findall(r"^([A-Za-z_-]+):", fm, re.M)
    extra = [k for k in keys if k not in ("name", "description")]
    if extra:
        issues.append("frontmatter should be name + description only; extra key(s): " + ", ".join(extra))

    name_m = re.search(r"^name:\s*(.+)$", fm, re.M)
    if not name_m:
        issues.append("frontmatter is missing 'name'")
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name_m.group(1).strip()):
        issues.append("'name' should be lowercase-kebab (got: %r)" % name_m.group(1).strip())

    desc = None
    for i, line in enumerate(fm_lines):
        m = re.match(r"^description:\s*(.*)$", line)
        if m:
            val = m.group(1).strip()
            if val in (">", "|", ">-", "|-", ""):
                desc = fold_description(fm_lines, i + 1, val)
            else:
                desc = val
            break
    if desc is None:
        issues.append("frontmatter is missing 'description'")
    elif len(desc) > SKILL_DESC_LIMIT:
        issues.append("description is %d characters — must be at most %d (the plugin loader "
                      "REJECTS the skill otherwise); trim by %d"
                      % (len(desc), SKILL_DESC_LIMIT, len(desc) - SKILL_DESC_LIMIT))
    return issues


def check_plugin_json(txt):
    issues = []
    try:
        data = json.loads(txt)
    except Exception as e:
        return ["plugin.json is not valid JSON: %s" % e]
    name = data.get("name")
    if not name:
        issues.append("plugin.json is missing 'name'")
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(name)):
        issues.append("plugin.json 'name' should be lowercase-kebab (got: %r)" % name)
    desc = data.get("description")
    if not desc:
        issues.append("plugin.json is missing 'description'")
    elif len(str(desc)) > PLUGIN_DESC_LIMIT:
        issues.append("plugin.json description is %d characters — must be at most %d (the plugin "
                      "loader REJECTS it otherwise); trim by %d"
                      % (len(str(desc)), PLUGIN_DESC_LIMIT, len(str(desc)) - PLUGIN_DESC_LIMIT))
    return issues


try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

fp = (payload.get("tool_input") or {}).get("file_path", "")
norm = fp.replace("\\", "/")
is_skill = norm.endswith("/SKILL.md") or norm == "SKILL.md"
is_manifest = norm.endswith("/.claude-plugin/plugin.json")
if not (is_skill or is_manifest):
    sys.exit(0)

try:
    raw = open(fp, "rb").read()
except Exception:
    sys.exit(0)

issues = []
if is_skill and b"\r\n" in raw:
    issues.append("CRLF line endings — must be LF (CRLF breaks the skills-viewer frontmatter parse)")

txt = raw.decode("utf-8", errors="ignore")
issues += check_skill_md(txt) if is_skill else check_plugin_json(txt)

if issues:
    msg = ("Skill/plugin hygiene — " + fp + ": " + "; ".join(issues) +
           ". Fix before packaging/relying on it.")
    print(json.dumps({"systemMessage": msg, "additionalContext": msg,
                      "hookSpecificOutput": {"hookEventName": "PostToolUse"}}))
sys.exit(0)
