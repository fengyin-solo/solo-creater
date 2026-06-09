#!/usr/bin/env python3
"""Create many GitHub repos from one source repository for solo-create."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    slug: str
    count: int


def run_command(
    args: list[str],
    cwd: Path | None = None,
    check: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        command = " ".join(args)
        message = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(f"{command}: {message}")
    return result


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Missing required command: {name}")


def resolve_gh_bin(value: str) -> str:
    gh_bin = value or shutil.which("gh") or ""
    if not gh_bin:
        raise SystemExit("Missing required command: gh")
    return gh_bin


def ensure_official_gh(gh_bin: str) -> None:
    version = run_command([gh_bin, "--version"])
    help_result = run_command([gh_bin, "repo", "create", "--help"])
    version_text = f"{version.stdout}\n{version.stderr}"
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    valid_help_markers = (
        "Create a new repository",
        "Create a new GitHub repository",
    )
    if "gh version" not in version_text or not any(marker in help_text for marker in valid_help_markers):
        raise SystemExit(
            "The configured gh command does not look like the official GitHub CLI. "
            "Install the official GitHub CLI or pass --gh-bin with its full path."
        )


def git_status_porcelain(repo: Path) -> str:
    result = run_command(["git", "status", "--porcelain"], cwd=repo, check=True)
    return result.stdout.strip()


def default_branch_name(repo: Path) -> str:
    result = run_command(["git", "branch", "--show-current"], cwd=repo)
    branch = result.stdout.strip()
    return branch or "main"


def extract_source_number(source: Path) -> str | None:
    match = re.search(r"\d{3,}", source.name)
    if match:
        return match.group(0)
    remote = run_command(["git", "remote", "get-url", "origin"], cwd=source)
    if remote.returncode == 0:
        match = re.search(r"\d{3,}", remote.stdout.strip())
        if match:
            return match.group(0)
    return None


def repo_exists(gh_bin: str, full_name: str) -> bool:
    result = run_command([gh_bin, "repo", "view", full_name])
    return result.returncode == 0


def copy_source_tree(source: Path, target: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {".git"} if ".git" in names else set()
        return ignored

    shutil.copytree(source, target, ignore=ignore)


def initial_commit(target: Path, branch: str) -> None:
    run_command(["git", "init", "-b", branch], cwd=target, check=True)
    run_command(["git", "add", "-A"], cwd=target, check=True)
    result = run_command(["git", "diff", "--cached", "--quiet"], cwd=target)
    if result.returncode == 0:
        raise RuntimeError("target tree has no files to commit")
    run_command(["git", "commit", "-m", "Initial source copy"], cwd=target, check=True)


def set_local_git_identity(target: Path, user_name: str | None, user_email: str | None) -> None:
    if user_name:
        run_command(["git", "config", "user.name", user_name], cwd=target, check=True)
    if user_email:
        run_command(["git", "config", "user.email", user_email], cwd=target, check=True)


def create_remote_repo(gh_bin: str, full_name: str, visibility: str) -> None:
    args = [gh_bin, "repo", "create", full_name, f"--{visibility}"]
    run_command(args, check=True)


def clean_https_remote(full_name: str) -> str:
    return f"https://github.com/{full_name}.git"


def configure_clean_origin(target: Path, full_name: str) -> None:
    run_command(["git", "remote", "remove", "origin"], cwd=target)
    run_command(["git", "remote", "add", "origin", clean_https_remote(full_name)], cwd=target, check=True)


def configure_clean_tracking(target: Path, branch: str) -> None:
    run_command(["git", "config", f"branch.{branch}.remote", "origin"], cwd=target, check=True)
    run_command(["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=target, check=True)
    run_command(["git", "config", "--unset-all", f"branch.{branch}.pushRemote"], cwd=target)


def push_initial_commit(gh_bin: str, target: Path, full_name: str, branch: str) -> None:
    configure_clean_origin(target, full_name)
    token = run_command([gh_bin, "auth", "token"], check=True).stdout.strip()
    if not token:
        raise RuntimeError("gh auth token returned an empty token")
    encoded_auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    push = run_command(
        [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "http.version=HTTP/1.1",
            "-c",
            f"http.https://github.com/.extraHeader=Authorization: Basic {encoded_auth}",
            "push",
            "origin",
            f"{branch}:{branch}",
        ],
        cwd=target,
    )
    configure_clean_origin(target, full_name)
    configure_clean_tracking(target, branch)
    if push.returncode != 0:
        message = push.stderr.strip() or push.stdout.strip() or "push failed"
        raise RuntimeError(f"git push {full_name}: {message}")


def remote_branch_exists(gh_bin: str, full_name: str, branch: str) -> bool:
    result = run_command([gh_bin, "api", f"repos/{full_name}/branches/{branch}", "--jq", ".name"])
    return result.returncode == 0 and result.stdout.strip() == branch


def build_specs(args: argparse.Namespace) -> list[TaskSpec]:
    return [
        TaskSpec("codegen", args.codegen_count),
        TaskSpec("feature", args.feature_count),
        TaskSpec("understand", args.understand_count),
        TaskSpec("refactor", args.refactor_count),
        TaskSpec("engineering", args.engineering_count),
    ]


def planned_repos(source_number: str, specs: list[TaskSpec]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    sequence = 1
    for spec in specs:
        for type_index in range(1, spec.count + 1):
            name = f"{source_number}-{spec.slug}-{sequence}"
            items.append({"name": name, "task_slug": spec.slug, "index": sequence, "type_index": type_index})
            sequence += 1
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", required=True, help="Parent directory where local repos are created")
    parser.add_argument("--source", required=True, help="Source repository directory")
    parser.add_argument("--owner", help="GitHub user or organization. If omitted, gh uses the authenticated default")
    parser.add_argument("--visibility", choices=["public", "private", "internal"], default="public")
    parser.add_argument("--source-number", help="Project number used in repo names")
    parser.add_argument("--codegen-count", type=int, default=20)
    parser.add_argument("--feature-count", type=int, default=20)
    parser.add_argument("--understand-count", type=int, default=1)
    parser.add_argument("--refactor-count", type=int, default=1)
    parser.add_argument("--engineering-count", type=int, default=1)
    parser.add_argument("--gh-bin", default="", help="Path to the official GitHub CLI")
    parser.add_argument("--git-user-name", help="Local git user.name to write into generated repos before commit")
    parser.add_argument("--git-user-email", help="Local git user.email to write into generated repos before commit")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow copying a dirty source worktree")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    require_command("git")
    gh_bin = resolve_gh_bin(args.gh_bin)
    ensure_official_gh(gh_bin)

    parent = Path(args.parent).expanduser().resolve()
    source = Path(args.source).expanduser().resolve()
    if not parent.is_dir():
        raise SystemExit(f"Parent directory does not exist: {parent}")
    if not source.is_dir():
        raise SystemExit(f"Source directory does not exist: {source}")
    if not (source / ".git").exists():
        raise SystemExit(f"Source is not a git repository: {source}")
    if source.parent != parent:
        raise SystemExit("Source repository must be an immediate child of parent")

    auth = run_command([gh_bin, "auth", "status"])
    if auth.returncode != 0:
        raise SystemExit(auth.stderr.strip() or "GitHub CLI is not authenticated")

    dirty = git_status_porcelain(source)
    if dirty and not args.allow_dirty:
        raise SystemExit("Source repository has uncommitted changes. Commit them or rerun with --allow-dirty.")

    source_number = args.source_number or extract_source_number(source)
    if not source_number:
        raise SystemExit("Cannot infer source project number. Rerun with --source-number.")

    branch = default_branch_name(source)
    specs = build_specs(args)
    plan = planned_repos(source_number, specs)
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for item in plan:
        repo_name = str(item["name"])
        full_name = f"{args.owner}/{repo_name}" if args.owner else repo_name
        target = parent / repo_name
        if target.exists():
            if not args.dry_run and (target / ".git").exists() and repo_exists(gh_bin, full_name):
                try:
                    configure_clean_origin(target, full_name)
                    if not remote_branch_exists(gh_bin, full_name, branch):
                        push_initial_commit(gh_bin, target, full_name, branch)
                        created.append({"repo": full_name, "path": str(target), "status": "pushed-existing-local"})
                    else:
                        skipped.append({"repo": full_name, "path": str(target), "reason": "local target and remote branch exist"})
                except Exception as exc:  # noqa: BLE001
                    failed.append({"repo": full_name, "path": str(target), "reason": str(exc)})
            else:
                skipped.append({"repo": full_name, "path": str(target), "reason": "local target exists"})
            continue
        if args.dry_run:
            created.append({"repo": full_name, "path": str(target), "status": "planned"})
            continue
        try:
            copy_source_tree(source, target)
            set_local_git_identity(target, args.git_user_name, args.git_user_email)
            initial_commit(target, branch)
            if not repo_exists(gh_bin, full_name):
                create_remote_repo(gh_bin, full_name, args.visibility)
            push_initial_commit(gh_bin, target, full_name, branch)
            created.append({"repo": full_name, "path": str(target), "status": "created"})
        except Exception as exc:  # noqa: BLE001
            failed.append({"repo": full_name, "path": str(target), "reason": str(exc)})

    print(
        json.dumps(
            {
                "parent": str(parent),
                "source": str(source),
                "source_number": source_number,
                "branch": branch,
                "visibility": args.visibility,
                "dry_run": args.dry_run,
                "planned_count": len(plan),
                "created_count": len(created) if not args.dry_run else 0,
                "dry_run_count": len(created) if args.dry_run else 0,
                "skipped_count": len(skipped),
                "failed_count": len(failed),
                "created": created,
                "skipped": skipped,
                "failed": failed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
