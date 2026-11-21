#!/bin/sh
# Publish only reviewed browser assets from the exact main commit, preserving Pages history.
set -eu
repo=bowierliu/readmission-review-agent
cd "$(git rev-parse --show-toplevel)"
target=$(git remote get-url origin)
case "$target" in
  "https://github.com/$repo"|"https://github.com/$repo.git"|"git@github.com:$repo.git") ;;
  *) echo "Expected the standalone GitHub repository $repo." >&2; exit 1;;
esac
git fetch --quiet origin main
source=$(git rev-parse HEAD)
if [ "$source" != "$(git rev-parse FETCH_HEAD)" ]; then
  echo "Publish the reviewed main commit before its demo." >&2
  exit 1
fi

# Use a temporary Git index. Neither the checkout nor the normal staging area is modified.
work=$(mktemp -d)
trap 'rm -f "$work/index" "$work/index.lock"; rmdir "$work"' EXIT
export GIT_INDEX_FILE="$work/index"
git read-tree --empty
for asset in index.html assets/app.js assets/app.css assets/recordings.js; do
  blob=$(git rev-parse --verify "$source:docs/$asset")
  git update-index --add --cacheinfo "100644,$blob,$asset"
done
marker=$(git hash-object -w --stdin </dev/null)
git update-index --add --cacheinfo "100644,$marker,.nojekyll"
tree=$(git write-tree)

# Preserve the previous branch and let Git reject concurrent updates.
old=$(git ls-remote --heads origin refs/heads/gh-pages)
if [ -n "$old" ]; then
  git fetch --quiet origin gh-pages
  parent=$(git rev-parse FETCH_HEAD)
  if [ "$tree" = "$(git rev-parse "$parent^{tree}")" ]; then
    echo "Pages assets match the reviewed main commit."
    exit 0
  fi
  commit=$(git commit-tree "$tree" -p "$parent" -m "Publish static demo from $source")
else
  commit=$(git commit-tree "$tree" -m "Publish static demo from $source")
fi
git push origin "$commit:refs/heads/gh-pages"
