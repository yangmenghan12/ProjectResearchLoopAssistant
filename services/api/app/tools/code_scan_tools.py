from pathlib import Path
from os import walk

from app.tools.file_tools import read_text_excerpt
from app.tools.path_policy import should_skip_path, to_relative_posix


DEPENDENCY_FILE_NAMES = {
    "go.mod",
    "package-lock.json",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
}
CONFIG_FILE_NAMES = {
    ".editorconfig",
    ".gitignore",
    "docker-compose.yml",
    "Dockerfile",
    "eslint.config.js",
    "next.config.ts",
    "pytest.ini",
    "tsconfig.json",
}
ENTRYPOINT_FILE_NAMES = {
    "app.py",
    "main.py",
    "server.py",
}
ENTRYPOINT_RELATIVE_PATHS = {
    "app/main.py",
    "src/index.js",
    "src/index.ts",
    "src/main.js",
    "src/main.ts",
}


def detect_stack(relative_path: str) -> set[str]:
    lowered = relative_path.lower()
    stack: set[str] = set()
    if lowered.endswith(".py") or lowered.endswith("pyproject.toml") or lowered.endswith("requirements.txt"):
        stack.add("python")
    if lowered.endswith((".ts", ".tsx")) or lowered.endswith("tsconfig.json"):
        stack.add("typescript")
    if lowered.endswith((".js", ".jsx")) or lowered.endswith("package.json"):
        stack.add("javascript")
    if lowered.endswith("go.mod") or lowered.endswith(".go"):
        stack.add("go")
    if lowered.endswith("pom.xml") or lowered.endswith(".java"):
        stack.add("java")
    return stack


def is_test_file(relative_path: str) -> bool:
    lowered = relative_path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def scan_codebase(root: Path, max_file_bytes: int, excerpt_chars: int) -> dict:
    """扫描代码目录的结构线索；不执行脚本，不跟随 symlink。"""

    root = root.resolve()
    tech_stack: set[str] = set()
    dependency_files: list[str] = []
    config_files: list[str] = []
    test_files: list[str] = []
    entrypoint_files: list[str] = []
    file_tree: list[dict] = []
    skipped_items: list[dict] = []
    readme_excerpt: str | None = None

    for current_root_raw, dir_names, file_names in walk(root, topdown=True, followlinks=False):
        current_root = Path(current_root_raw)
        kept_dir_names: list[str] = []
        for dir_name in sorted(dir_names):
            dir_path = current_root / dir_name
            decision = should_skip_path(dir_path, root, max_file_bytes)
            if decision is not None:
                skipped_items.append(decision)
                continue
            kept_dir_names.append(dir_name)
            file_tree.append({"path": to_relative_posix(dir_path, root), "kind": "directory"})
        dir_names[:] = kept_dir_names

        for file_name in sorted(file_names):
            file_path = current_root / file_name
            decision = should_skip_path(file_path, root, max_file_bytes)
            if decision is not None:
                skipped_items.append(decision)
                continue

            relative_path = to_relative_posix(file_path, root)
            lowered_name = file_name.lower()
            file_tree.append(
                {
                    "path": relative_path,
                    "kind": "file",
                    "size_bytes": file_path.stat().st_size,
                }
            )
            tech_stack.update(detect_stack(relative_path))

            if file_name in DEPENDENCY_FILE_NAMES:
                dependency_files.append(relative_path)
            if file_name in CONFIG_FILE_NAMES:
                config_files.append(relative_path)
            if is_test_file(relative_path):
                test_files.append(relative_path)
            if file_name in ENTRYPOINT_FILE_NAMES or relative_path in ENTRYPOINT_RELATIVE_PATHS:
                entrypoint_files.append(relative_path)
            if readme_excerpt is None and lowered_name.startswith("readme"):
                readme_excerpt = read_text_excerpt(file_path, excerpt_chars)

    return {
        "root_label": root.name,
        "tech_stack": sorted(tech_stack),
        "dependency_files": sorted(dependency_files),
        "config_files": sorted(config_files),
        "test_files": sorted(test_files),
        "entrypoint_files": sorted(entrypoint_files),
        "readme_excerpt": readme_excerpt,
        "file_tree": file_tree,
        "skipped_items": skipped_items,
        "metadata": {
            "root_path": str(root),
            "total_files": sum(1 for item in file_tree if item["kind"] == "file"),
            "total_directories": sum(1 for item in file_tree if item["kind"] == "directory"),
        },
    }
