#!/usr/bin/bash
set -euo pipefail

sleep_time='0.42'
##set -x

sleep "$sleep_time"
time (
  git diff | head --lines=17 || true
  sleep "$sleep_time"
  echo git pull --verbose
  git log --raw --no-merges --max-count=5 | head --lines=13 || true

  git_commits_counter="$(git rev-list --all --count)"
  git_commits_counter=$(( git_commits_counter + 0 ))
  rand_prefix="$(printf '%06x' "$(( (RANDOM << 16) ^ RANDOM ))")"
  script_version="${rand_prefix}-${git_commits_counter}-$(date "+%y%m%d%H%M")"

  echo " ● ${script_version} ● "

  sleep "$sleep_time"
  git add --verbose --all
  sleep "$sleep_time"

  if ! git diff --cached --quiet; then
    git commit --allow-empty-message --message="$script_version" --verbose
  else
    echo "info: no staged changes, skip commit"
  fi

  git push --verbose origin HEAD:main
  echo " ● ${script_version} ● "

  # check all syntax without running
  find . -name '*.sh' -print0 | xargs -0 -P"$(nproc)" -I {} bash -n "{}"
)
