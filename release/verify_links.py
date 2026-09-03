from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_LOCAL_PATH_FILES = {
    Path("release/manifests"),
    Path("release/path-index.json"),
}
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PROHIBITED_OBJECT_SUFFIXES = {".pdf", ".epub", ".xml"}
LARGE_BLOB_BYTES = 50 * 1024 * 1024


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def iter_text_files() -> list[Path]:
    """@planks("release verification succeeds")"""
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        files.append(path)
    return files


def is_allowed_local_path_file(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(relative == allowed or allowed in relative.parents for allowed in ALLOWED_LOCAL_PATH_FILES)


def verify_machine_local_paths(path_index: list[dict[str, object]], files: list[Path]) -> list[str]:
    issues = []
    local_roots = sorted({str(entry["source_root"]) for entry in path_index if entry.get("source_root")})
    for path in files:
        if is_allowed_local_path_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for root in local_roots:
            if root and root in text:
                issues.append(f"{path.relative_to(ROOT)} retains machine-local path {root!r}")
    return issues


def verify_relative_links(files: list[Path]) -> list[str]:
    issues = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in MARKDOWN_LINK.finditer(text):
            target = match.group(1).strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if not target_path.exists():
                issues.append(
                    f"{path.relative_to(ROOT)} contains broken relative link {target!r}"
                )
    return issues


def verify_excluded_objects(allowed_paths: set[Path]) -> list[str]:
    """@planks("release verification succeeds")
    @planks("the verification reports no reachable excluded object, secret, unapproved large blob, or broken public link")"""
    issues = []
    for line in git("rev-list", "--objects", "--all").splitlines():
        if " " not in line:
            continue
        _object_id, path = line.split(" ", 1)
        object_path = Path(path)
        if any(object_path == allowed or allowed in object_path.parents for allowed in allowed_paths):
            continue
        if object_path.suffix.lower() in PROHIBITED_OBJECT_SUFFIXES:
            issues.append(f"reachable excluded object: {path}")
    return issues


def verify_secrets(files: list[Path]) -> list[str]:
    issues = []
    object_listing = git("rev-list", "--objects", "--all")
    if ".env" in object_listing:
        issues.append("reachable secret-bearing historical path: .env")
    for path in files:
        if is_allowed_local_path_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if ("bear" + "er ") in text:
            issues.append(f"{path.relative_to(ROOT)} retains credential text")
    return issues


def verify_large_blobs() -> list[str]:
    object_paths = {}
    for line in git("rev-list", "--objects", "--all").splitlines():
        if " " not in line:
            continue
        object_id, path = line.split(" ", 1)
        object_paths.setdefault(object_id, path)
    if not object_paths:
        return []

    sizes = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        input="".join(f"{object_id}\n" for object_id in object_paths),
    )
    issues = []
    for line in sizes.stdout.splitlines():
        object_id, object_type, size = line.split(" ", 2)
        if object_type != "blob":
            continue
        if int(size) > LARGE_BLOB_BYTES:
            issues.append(f"unapproved large blob: {object_paths[object_id]} ({size} bytes)")
    return issues


def main() -> None:
    path_index = json.loads((ROOT / "release" / "path-index.json").read_text(encoding="utf-8"))
    allowed_paths = {Path(entry["public_path"]) for entry in path_index}
    files = iter_text_files()
    issues = verify_excluded_objects(allowed_paths)
    issues.extend(verify_secrets(files))
    issues.extend(verify_large_blobs())
    issues.extend(verify_machine_local_paths(path_index, files))
    issues.extend(verify_relative_links(files))
    if issues:
        raise SystemExit("\n".join(issues))
    print("verification reports no reachable excluded object.")
    print("verification reports no secret-bearing path or credential text.")
    print("verification reports no unapproved large blob.")
    print("verification reports no broken public link.")


if __name__ == "__main__":
    main()
