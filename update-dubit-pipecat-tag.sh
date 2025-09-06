#!/bin/bash

set -e

changelog="CHANGELOG.md"

if [ ! -f "$changelog" ]; then
	echo "CHANGELOG.md not found."
	exit 1
fi

unreleased_line=$(grep -n '^## \[Unreleased\]' "$changelog" | cut -d: -f1)

if [ -z "$unreleased_line" ]; then
	latest_version_line=$(grep -n -m1 '^## \[[0-9]\+\.[0-9]\+\.[0-9]\+\]' "$changelog" | cut -d: -f1)
	if [ -z "$latest_version_line" ]; then
		echo "No stable version found in changelog."
		exit 1
	fi
	latest_version=$(sed -n "${latest_version_line}p" "$changelog" | sed -E 's/^## \[(.*)\] - .*/\1/')
	unreleased_changes_start=1
	unreleased_changes_end=$((latest_version_line - 2))
	has_unreleased=false
else
	latest_version_line=$(tail -n +$((unreleased_line + 1)) "$changelog" | grep -n -m1 '^## \[[0-9]\+\.[0-9]\+\.[0-9]\+\]' | cut -d: -f1)
	if [ -z "$latest_version_line" ]; then
		echo "No stable version found after [Unreleased]."
		exit 1
	fi
	latest_version=$(tail -n +$((unreleased_line + latest_version_line)) "$changelog" | head -n1 | sed -E 's/^## \[(.*)\] - .*/\1/')
	unreleased_changes_start=$((unreleased_line + 1))
	unreleased_changes_end=$((unreleased_line + latest_version_line - 2))
	has_unreleased=true
fi

echo "Latest stable version: $latest_version"

read -p "Confirm latest stable version? (y / (default) n): " confirm
if [ "$confirm" != "y" ]; then
	echo "Exiting."
	exit 0
fi

version_gt() {
	test "$(printf '%s\n' "$@" | sort -V | head -n 1)" != "$1"
}

prev_tag=$(git describe --tags --abbrev=0 2>/dev/null || true)

if [ -n "$prev_tag" ]; then
	prev_version=${prev_tag#v}
	prev_base_version=$(echo "$prev_version" | sed 's/+.*//')
	echo "Previous tagged version: $prev_version (base: $prev_base_version)"
	if [ "$prev_base_version" == "$latest_version" ]; then
		echo "Latest stable is already tagged (ignoring local labels)."
	elif version_gt "$latest_version" "$prev_base_version"; then
		echo "Latest stable ($latest_version) is newer than previous tag base ($prev_base_version)."
	else
		echo "Mismatch: latest in changelog ($latest_version) is older than previous tag base ($prev_base_version)."
		exit 1
	fi
	git_diff=$(git diff "$prev_tag" -- "$changelog" || true)
	if [ -n "$git_diff" ]; then
		echo "Changelog diff since $prev_tag:"
		if command -v bat &>/dev/null; then
			echo "$git_diff" | bat --language=diff --style=plain
		else
			echo "$git_diff"
		fi
		read -p "Confirm changelog diff? (y / (default) n): " confirm
		if [ "$confirm" != "y" ]; then
			echo "Exiting."
			exit 0
		fi
	else
		other_diff=$(git diff "$prev_tag" || true)
		if [ -n "$other_diff" ]; then
			echo "No changes in CHANGELOG.md, but changes in other files since $prev_tag:"
			if command -v bat &>/dev/null; then
				echo "$other_diff" | bat --language=diff --style=plain
			else
				echo "$other_diff"
			fi
			read -p "Confirm other changes? (y / (default) n): " confirm
			if [ "$confirm" != "y" ]; then
				echo "Exiting."
				exit 0
			fi
		else
			echo "No changes since $prev_tag."
			read -p "Proceed anyway? (y / (default) n): " confirm
			if [ "$confirm" != "y" ]; then
				echo "Exiting."
				exit 0
			fi
		fi
	fi
else
	echo "No previous tag found."
	read -p "Do you want to see entire changelog? (y / (default) n): " confirm
	if [ "$confirm" == "y" ]; then
		echo "Entire changelog:"
		if command -v bat &>/dev/null; then
			bat --language=md --style=plain "$changelog"
		else
			cat "$changelog"
		fi
		read -p "Confirm? (y / (default) n): " confirm
		if [ "$confirm" != "y" ]; then
			echo "Exiting."
			exit 0
		fi
	fi
fi

if [ "$has_unreleased" = true ] && [ $unreleased_changes_end -ge $unreleased_changes_start ]; then
	echo "Unreleased changes:"
	if command -v bat &>/dev/null; then
		sed -n "${unreleased_changes_start},${unreleased_changes_end}p" "$changelog" | bat --language=md --style=plain
	else
		sed -n "${unreleased_changes_start},${unreleased_changes_end}p" "$changelog"
	fi
	read -p "Confirm unreleased changes? (y / (default) n): " confirm
	if [ "$confirm" != "y" ]; then
		echo "Exiting."
		exit 0
	fi
elif [ "$has_unreleased" = false ] && [ $unreleased_changes_end -ge $unreleased_changes_start ]; then
	echo "Changes before latest stable (possible unreleased):"
	if command -v bat &>/dev/null; then
		sed -n "${unreleased_changes_start},${unreleased_changes_end}p" "$changelog" | bat --language=md --style=plain
	else
		sed -n "${unreleased_changes_start},${unreleased_changes_end}p" "$changelog"
	fi
	read -p "Confirm changes? (y / (default) n): " confirm
	if [ "$confirm" != "y" ]; then
		echo "Exiting."
		exit 0
	fi
else
	echo "No unreleased changes."
	read -p "Proceed anyway? (y / (default) n): " confirm
	if [ "$confirm" != "y" ]; then
		echo "Exiting."
		exit 0
	fi
fi

current_date=$(date +%Y-%m-%d)
if [ "$has_unreleased" = true ]; then
	tag="v${latest_version}+dubit-beta-${current_date}"
else
	tag="v${latest_version}+dubit-${current_date}"
fi

echo "Preparing tag: $tag"

# Add a patch counter if the tag already exists
if git tag | grep -q "^${tag}$"; then
    echo "Tag $tag already exists."
    # Initialize counter for unique identifier
    counter=1
    new_tag="${tag}-${counter}"
    # Find the next available tag by incrementing the counter
    while git tag | grep -q "^${new_tag}$"; do
        counter=$((counter + 1))
        new_tag="${tag}-${counter}"
    done
    tag="$new_tag"
    echo "Using new tag: $tag"
fi

read -p "Confirm and push tag? (y / (default) n): " confirm
if [ "$confirm" != "y" ]; then
	echo "Exiting."
	exit 0
fi

git tag "$tag"
git push origin "$tag"

echo "Tag pushed successfully."
