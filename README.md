# git-rg Scoop Bucket

This bucket distributes the verified Windows archives published by
[`SamuelSupe/git-rg`](https://github.com/SamuelSupe/git-rg). The package is a
prebuilt CLI: searching a remote repository still uses the GitHub/GitLab API,
so installing it never clones or checks out the repository being searched.

## Install

```powershell
scoop bucket add samuelsupe https://github.com/SamuelSupe/scoop-bucket
scoop install git-rg
git-rg --version
```

Upgrade and uninstall:

```powershell
scoop update
scoop update git-rg
scoop uninstall git-rg
```

The manifest supports Windows amd64 (x86_64) and arm64. Users who need Linux
or macOS should use the Homebrew tap, the Release archives, or the POSIX
installer.

## Integrity and update policy

`scripts/generate-manifest.py` selects the highest non-draft, non-prerelease
`vX.Y.Z` release at or after `v0.2.0`. It requires all nine release assets:
the six platform archives, `install.sh`, `install.ps1`, and `checksums.txt`.
It validates every required checksum entry and downloads each required payload
to compare SHA-256 before writing `bucket/git-rg.json`. Invalid versions,
missing assets, malformed or duplicate checksum entries, and checksum
mismatches reject the update.

The hourly workflow and `workflow_dispatch` produce a candidate manifest first.
Native Windows jobs (`windows-2025` and `windows-11-arm`) install Scoop, install
the candidate manifest, run `git-rg --version`, and uninstall it. Only after
both jobs pass does the workflow use its own `GITHUB_TOKEN` to commit a changed
manifest. If the manifest is already current, no commit is created.

The CI installer downloads a pinned Scoop installer commit into the runner temp
directory and verifies its SHA-256 before execution. It detects whether the job
is elevated and passes `-RunAsAdmin` only in that case; this handles the elevated
Windows hosted-runner image while keeping normal user installations non-admin.

`bucket/git-rg.json` is generated. Change the generator when the release
contract changes; do not hand-edit a generated update without reviewing the
next candidate diff.

## Local generation

From the root of this bucket, with Python 3:

```sh
python3 scripts/generate-manifest.py --output bucket/git-rg.json
```

The script prints the selected release tag on stdout and diagnostics on
stderr. If `GITHUB_TOKEN` is present, it is used only in memory as a Bearer
`Authorization` header for GitHub API and Release requests; it is never printed
or persisted. Without a token, public GitHub API rate limits still apply.

## License

The manifest, generator, workflow, and documentation in this bucket are
licensed under [MIT](LICENSE).
