#!/usr/bin/env bash
# publish_github.sh - refresh the public GitHub mirror of this repository.
#
# The NAS bare repo (origin) stays the master. GitHub holds a read-only copy for the
# team and ORI, with the private working notes (CLAUDE.md) removed from EVERY commit:
# a fresh clone of the local repo is rewritten with git filter-repo, then force-pushed.
# The rewrite is deterministic, so repeated runs produce the same history and the
# mirror's commit ids stay stable; tags are carried across.
#
#   bash tools/publish_github.sh              # mirror main + tags to K0GD/eve-modem
#   GITHUB_REPO=dses-science/eve-modem bash tools/publish_github.sh
#
# Run it after pushing to origin (and as the last step of a release cut). Needs
# git filter-repo (pip install git-filter-repo) and a GitHub login (gh auth login,
# or a credential helper for https://github.com).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${GITHUB_REPO:-K0GD/eve-modem}"
URL="https://github.com/${REPO}.git"
EXCLUDE=(CLAUDE.md)                 # private working notes; add paths here if needed

if [ -n "$(git -C "$here" status --porcelain)" ]; then
    echo "working tree not clean; commit or stash first" >&2
    exit 1
fi
if ! git -C "$here" filter-repo --version >/dev/null 2>&1; then
    echo "git filter-repo is not installed (pip install git-filter-repo)" >&2
    exit 1
fi
case "$(uname)" in
    MINGW*|MSYS*)
        # Git for Windows: use the Windows certificate store. A stale user gitconfig on
        # this machine points http.sslCAInfo at a Vivado bundle that no longer exists.
        export GIT_SSL_BACKEND=schannel ;;
esac
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "== cloning the local repo into $work"
git clone -q --no-local "$here" "$work/mirror"
cd "$work/mirror"
args=()
for p in "${EXCLUDE[@]}"; do args+=(--path "$p"); done
echo "== removing ${EXCLUDE[*]} from every commit"
git filter-repo --quiet --invert-paths "${args[@]}"
if git log --all --name-only --format= -- "${EXCLUDE[@]}" | grep -q .; then
    echo "filter failed: excluded paths still present" >&2
    exit 1
fi
echo "== $(git rev-list --count main) commits, $(git tag | wc -l | tr -d ' ') tags after filtering"
git remote add github "$URL"
echo "== pushing to $URL"
git push -q --force github main
git push -q --force github --tags
echo "== done: https://github.com/${REPO}"
