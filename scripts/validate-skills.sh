#!/usr/bin/env bash
# Structural lint for skills/ and the plugin manifests.
#
# Catches the failures that make a skill silently never load: a `name` that does
# not match its directory, a missing or over-long `description`, malformed
# frontmatter, invalid JSON manifests. It cannot check whether a skill is
# CORRECT — that is review, plus an actual run against production.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2

fail=0
err() { printf '  FAIL  %s\n' "$*"; fail=1; }
warn() { printf '  warn  %s\n' "$*"; }

need_json() {
  local f=$1
  [[ -f $f ]] || { err "$f: missing"; return; }
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$f" \
      || err "$f: invalid JSON"
  else
    warn "$f: python3 absent, JSON not validated"
  fi
}

echo "manifests"
need_json .claude-plugin/marketplace.json
need_json .claude-plugin/plugin.json

echo "skills"
shopt -s nullglob
found=0
for dir in skills/*/; do
  slug=${dir%/}; slug=${slug#skills/}
  f="${dir}SKILL.md"

  # A leading underscore marks a non-skill (scaffolding, a template). The loader
  # ignores it; so do we, or placeholder frontmatter fails every run.
  if [[ $slug == _* ]]; then
    printf '  skip  %s (not a skill)\n' "$slug"
    continue
  fi

  [[ -f $f ]] || { err "$slug: no SKILL.md"; continue; }
  found=$((found + 1))

  # Frontmatter must open on line 1 and close.
  [[ $(head -n1 "$f") == '---' ]] || { err "$slug: SKILL.md must start with '---'"; continue; }
  close=$(awk 'NR>1 && /^---[[:space:]]*$/ {print NR; exit}' "$f")
  [[ -n $close ]] || { err "$slug: frontmatter not closed"; continue; }

  fm=$(sed -n "2,$((close - 1))p" "$f")

  name=$(printf '%s\n' "$fm" | sed -n 's/^name:[[:space:]]*//p' | head -n1)
  desc=$(printf '%s\n' "$fm" | sed -n 's/^description:[[:space:]]*//p' | head -n1)

  [[ -n $name ]] || err "$slug: frontmatter missing 'name'"
  [[ -n $desc ]] || err "$slug: frontmatter missing 'description'"

  if [[ -n $name && $name != "$slug" ]]; then
    err "$slug: name '$name' does not match directory"
  fi
  [[ $slug =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || err "$slug: directory must be kebab-case"

  if [[ -n $desc ]]; then
    n=${#desc}
    (( n >= 80 ))   || err "$slug: description ${n}c — too thin to route on (aim 300-600)"
    (( n <= 1024 )) || err "$slug: description ${n}c — over 1024, will be truncated"
    (( n > 600 ))   && warn "$slug: description ${n}c — long; every session pays for it"
    shopt -s nocasematch
    [[ $desc == *"use this skill"* ]] && warn "$slug: description says 'use this skill' — write third person"
    shopt -u nocasematch
  fi

  # Unknown frontmatter keys are silently ignored by the loader — surface them.
  while IFS= read -r key; do
    case $key in
      name|description|'') ;;
      *) warn "$slug: unrecognized frontmatter key '$key'" ;;
    esac
  done < <(printf '%s\n' "$fm" | sed -n 's/^\([a-zA-Z_-]*\):.*/\1/p')

  # Semantic-rule forking: the highest-value check here. See docs/graph-semantics.md.
  # Collapse whitespace first: these phrases routinely straddle a line wrap,
  # and a line-oriented grep misses exactly the copies worth catching.
  if tr '\n' ' ' < "$f" | tr -s '[:space:]' ' ' \
     | grep -qiE 'lowercase hex|ADMIN_CTRL runs|zero rows means|DEPENDS_ON is a category|is not an edge type'; then
    warn "$slug: looks like it restates a server semantic rule — reference it by number instead"
  fi

  lines=$(wc -l < "$f")
  (( lines > 500 )) && warn "$slug: ${lines} lines — move reference material to references/"

  printf '  ok    %s (%sc description, %s lines)\n' "$slug" "${#desc}" "$lines"
done

(( found > 0 )) || err "no skills found under skills/"

echo
if (( fail )); then
  echo "FAILED"
  exit 1
fi
echo "OK — $found skill(s). Structure only; correctness needs a run against production."
