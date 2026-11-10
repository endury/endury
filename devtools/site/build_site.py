"""Build the static site (``site/``) that endury.app serves.

The output is two independently cache-busted trees, both derived purely
from file contents so an edit anywhere changes the URL that names it:

- ``web/`` is copied to ``site/`` (unversioned — the manifest, service
  worker, legal pages and icons resolve here by their plain relative URLs)
  and again to ``site/<build-id>/`` (versioned — content-addressed,
  cacheable forever). ``site/index.html`` is the one file rewritten: its
  relative ``.css``/``.js`` references are pinned to ``./<build-id>/...``,
  once, at build time — a static host just serves whatever bytes are on
  disk, and an installed PWA may ignore ``no-cache``.
- ``endury/**/*.py`` and ``*.yaml`` are zipped into
  ``site/py/endury-<sha>.zip``, fetched into the Pyodide worker at
  runtime.
- The Pyodide runtime and the packages the worker loads are copied to
  ``site/pyodide/v<version>/``, so the page fetches nothing from a third
  party. They are downloaded once from Pyodide's release on jsDelivr into
  ``.pyodide/`` (each wheel checked against the sha256 in Pyodide's lock);
  ``web/worker/py_worker.js`` is the one home of the version and the
  package list, read from here.

Only files git knows about (tracked, or untracked and not ignored) are
built, so a stray scratch file never ships; without git, every file on
disk is.

The legal pages name whoever hosts the site. Those details live outside the
repository, in ``~/.config/endury/legal.json``, and are written into
``site/`` only; ``make deploy`` refuses a site whose legal pages are blank.

``site/version.json`` names the builds: ``{build, bundle, pyodide}``.

Usage:
    make site
    poetry run python -m devtools.site.build_site
"""

from __future__ import annotations

import email
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web"
ENDURY_DIR = REPO_ROOT / "endury"
SITE_DIR = REPO_ROOT / "site"
WORKER = WEB_DIR / "worker" / "py_worker.js"
PYODIDE_CACHE = REPO_ROOT / ".pyodide"
LEGAL_DETAILS = Path.home() / ".config" / "endury" / "legal.json"
LEGAL_PAGES = ("impressum.html", "privacy.html")
#: The marked places in the legal pages -> the key that fills each.
LEGAL_FIELDS = {
    "FULL NAME": "full_name",
    "STREET AND NUMBER": "street",
    "POSTCODE CITY": "city",
    "EMAIL ADDRESS": "email",
}

PYODIDE_RELEASE = "https://cdn.jsdelivr.net/pyodide/v{version}/full/"
#: Licence texts for what has none beside it on the site: the runtime and the
#: libraries compiled into it and into scipy.
RUNTIME_LICENSES = REPO_ROOT / "devtools" / "site" / "runtime_licenses"
#: The core files by release: Pyodide's lock does not hash them. A version bump
#: in py_worker.js stops the build until its digests are added here.
PYODIDE_CORE_SHA256 = {
    "314.0.7": {
        "pyodide.mjs": "6f1d60f7bf529beb300f0f47983c921d3982363640ba20af0e38efdddbc66109",
        "pyodide.asm.mjs": "f7cdc8ece80678ceb712f8e65ebe6d3a83203a180c399865f49612a051693635",
        "pyodide.asm.wasm": "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6",
        "python_stdlib.zip": "fa1957e5777068fc4f7437f96d860ae2fbe9c19732ba06c84e004ec16dd7dd7a",
        "pyodide-lock.json": "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010",
    },
}
#: The runtime itself; the packages come from its lock.
PYODIDE_CORE = (
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)

# Relative .css/.js references in index.html — see module docstring.
_ASSET_REF = re.compile(r'\b(href|src)="(?!https?:|/)([^"]+\.(?:css|js))"')
# The worker's pins: `.../pyodide/v314.0.7/` and `const PACKAGES = [...]`.
_PYODIDE_VERSION = re.compile(r"pyodide/v(\d+\.\d+\.\d+)/")
_PACKAGES = re.compile(r"const PACKAGES = \[([^\]]*)\]")


def _files(directory: Path, suffixes: tuple[str, ...] | None = None) -> list[Path]:
    """Return the files under ``directory`` that belong in the build.

    Args:
        directory: A directory inside the repository.
        suffixes: When given, keep only files with one of these suffixes.

    Returns:
        The files git knows about (tracked, or untracked and not ignored),
        sorted; every file on disk when git is unavailable.
    """
    try:
        listing = subprocess.run(
            [
                "git",
                "-C",
                str(REPO_ROOT),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                str(directory),
            ],
            capture_output=True,
            check=True,
        ).stdout.decode()
        files = [REPO_ROOT / name for name in listing.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        files = list(directory.rglob("*"))
    return sorted(
        p for p in files if p.is_file() and (suffixes is None or p.suffix in suffixes)
    )


def _content_hash(files: list[Path]) -> str:
    """Return a 12-hex sha256 digest over each file's repo-relative path and bytes.

    Args:
        files: Files to hash, in any order (sorted internally for a
            deterministic digest regardless of filesystem iteration order).

    Returns:
        The first 12 hex characters of the digest.
    """
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(REPO_ROOT).as_posix()):
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _write_versioned_web(build_id: str, files: list[Path]) -> None:
    """Copy ``web/`` to the site root and to ``site/<build_id>/``, then pin index.html.

    Args:
        build_id: The content hash naming the versioned copy.
        files: The ``web/`` files to copy.
    """
    for path in files:
        relative = path.relative_to(WEB_DIR)
        for dest in (SITE_DIR / relative, SITE_DIR / build_id / relative):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)

    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    versioned = _ASSET_REF.sub(rf'\1="./{build_id}/\2"', html)
    (SITE_DIR / "index.html").write_text(versioned, encoding="utf-8")


def _fill_legal_pages(pages: list[Path], details_file: Path) -> None:
    """Write the host's details into the marked places of the built legal pages.

    Args:
        pages: The built copies of the legal pages.
        details_file: JSON holding one value per key of ``LEGAL_FIELDS``. A
            missing file leaves the pages blank, which ``make deploy`` refuses.
    """
    if not details_file.exists():
        print(f"no {details_file}: the legal pages stay blank")
        return
    details = json.loads(details_file.read_text(encoding="utf-8"))
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for mark, key in LEGAL_FIELDS.items():
            filled = html.escape(details[key])
            text = text.replace(f'<span class="legal-fill">{mark}</span>', filled)
        page.write_text(text, encoding="utf-8")


def _build_python_bundle() -> str:
    """Zip ``endury/**/*.py`` and ``*.yaml`` into a content-addressed bundle.

    Returns:
        The bundle's filename, e.g. ``"endury-0123456789ab.zip"``.
    """
    py_files = _files(ENDURY_DIR, (".py", ".yaml"))
    name = f"endury-{_content_hash(py_files)}.zip"
    py_dir = SITE_DIR / "py"
    py_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(py_dir / name, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in py_files:
            bundle.write(path, path.relative_to(REPO_ROOT).as_posix())
    return name


def _worker_pins() -> tuple[str, list[str]]:
    """Return the Pyodide version and package list ``py_worker.js`` declares."""
    source = WORKER.read_text(encoding="utf-8")
    version = _PYODIDE_VERSION.search(source)
    packages = _PACKAGES.search(source)
    if not version or not packages:
        sys.exit(f"error: no Pyodide version or PACKAGES list in {WORKER}")
    return version.group(1), re.findall(r"'([^']+)'", packages.group(1))


def _download(url: str, dest: Path, sha256: str) -> None:
    """Fetch ``url`` into ``dest`` unless a copy (matching ``sha256``) is there.

    Args:
        url: The file's URL on Pyodide's release.
        dest: Where to keep it.
        sha256: The expected digest: Pyodide's lock for a wheel,
            ``PYODIDE_CORE_SHA256`` for the runtime's own files.
    """
    if dest.exists() and _sha256(dest) == sha256:
        return
    print(f"downloading {url}")
    with urllib.request.urlopen(url) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        sys.exit(f"error: {url} does not match its pinned sha256")
    dest.write_bytes(data)


def _sha256(path: Path) -> str:
    """Return the hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_files(lock: dict, packages: list[str]) -> list[tuple[str, str]]:
    """Return ``(file_name, sha256)`` for ``packages`` and all they depend on.

    Args:
        lock: Pyodide's ``pyodide-lock.json``.
        packages: The names the worker passes to ``loadPackage``.
    """

    def norm(name: str) -> str:
        return name.lower().replace("_", "-")

    entries = {norm(key): entry for key, entry in lock["packages"].items()}
    todo, seen = list(packages), []
    while todo:
        name = norm(todo.pop())
        if name in seen:
            continue
        seen.append(name)
        todo.extend(entries[name]["depends"])
    return [(entries[n]["file_name"], entries[n]["sha256"]) for n in sorted(seen)]


def _stage_pyodide() -> str:
    """Put the runtime and the worker's packages under ``site/pyodide/``.

    Returns:
        The Pyodide version staged.
    """
    version, packages = _worker_pins()
    cache = PYODIDE_CACHE / version
    cache.mkdir(parents=True, exist_ok=True)
    release = PYODIDE_RELEASE.format(version=version)
    core = PYODIDE_CORE_SHA256.get(version)
    if core is None:
        sys.exit(f"error: add Pyodide {version}'s digests to PYODIDE_CORE_SHA256")
    for name in PYODIDE_CORE:
        _download(release + name, cache / name, core[name])
    lock = json.loads((cache / "pyodide-lock.json").read_text(encoding="utf-8"))
    wheels = _package_files(lock, packages)
    for file_name, sha256 in wheels:
        _download(release + file_name, cache / file_name, sha256)

    dest = SITE_DIR / "pyodide" / f"v{version}"
    dest.mkdir(parents=True)
    for name in (*PYODIDE_CORE, *(file_name for file_name, _ in wheels)):
        shutil.copy2(cache / name, dest / name)
    (SITE_DIR / "THIRD_PARTY_LICENSES.txt").write_text(
        licenses_text(version, [cache / file_name for file_name, _ in wheels]),
        encoding="utf-8",
    )
    return version


def _crate_lines(sbom: bytes) -> list[str]:
    """Return one line per component a wheel's CycloneDX file lists."""
    lines = []
    for component in json.loads(sbom).get("components", []):
        licenses = " OR ".join(
            entry.get("expression") or entry.get("license", {}).get("id", "")
            for entry in component.get("licenses", [])
        )
        lines.append(
            f"  {component['name']} {component.get('version', '')}: {licenses}"
        )
    return lines


def _wheel_section(wheel: Path) -> str:
    """Return a wheel's name, version, licence, crates and licence texts.

    Args:
        wheel: The wheel as staged.
    """
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if ".dist-info/" in n]
        meta = email.message_from_bytes(
            archive.read(next(n for n in names if n.endswith("/METADATA")))
        )
        texts = [
            n
            for n in names
            if re.search(
                r"licen[cs]e|copying|notice", n.rsplit("/", 1)[-1], re.IGNORECASE
            )
            and not n.endswith(".json")
        ]
        if not texts:
            sys.exit(f"error: {wheel.name} carries no licence file")
        licence = meta["License-Expression"] or (meta["License"] or "").split("\n")[0]
        out = [
            "=" * 72,
            f"{meta['Name']} {meta['Version']} ({wheel.name})",
            f"Licence: {licence or 'see the text below'}",
            f"Upstream: https://pypi.org/project/{meta['Name']}/{meta['Version']}/",
        ]
        for name in names:
            if name.endswith(".cyclonedx.json"):
                out += ["Compiled in:", *_crate_lines(archive.read(name))]
        for name in texts:
            out += [
                "",
                f"--- {name.rsplit('/', 1)[-1]}",
                archive.read(name).decode("utf-8"),
            ]
    return "\n".join(out)


def licenses_text(version: str, wheels: list[Path]) -> str:
    """Return the site's THIRD_PARTY_LICENSES.txt.

    Args:
        version: The Pyodide release staged.
        wheels: The wheels staged.
    """
    head = (
        "Third-party software this site serves, beside Endury's own code\n"
        "(LICENSE, NOTICE, source/endury-source.tar.gz).\n\n"
        "The wheels are Pyodide's own builds, some patched. Exact sources:\n"
        f"  https://github.com/pyodide/pyodide/tree/{version}\n"
        "  https://github.com/pyodide/pyodide-recipes/tree/314-20260911 (source URL\n"
        "  and patches of each; httpx is built from hoodmane/httpx pyodide_release_5)\n\n"
        "The runtime (pyodide.asm.wasm, python_stdlib.zip) compiles in CPython with\n"
        "the libraries its licence page names, Emscripten's system libraries and musl.\n"
        "scipy compiles in OpenBLAS and the libraries of its LICENSES_bundled.\n"
    )
    runtime = [
        f"{'=' * 72}\n{path.name}\n\n{path.read_text(encoding='utf-8')}"
        for path in sorted(RUNTIME_LICENSES.iterdir())
    ]
    return "\n".join([head, *runtime, *map(_wheel_section, wheels)]) + "\n"


def _stage_source() -> str:
    """Serve the licence and the committed source; return the commit they are of."""
    shutil.copy2(REPO_ROOT / "LICENSE", SITE_DIR / "LICENSE")
    shutil.copy2(REPO_ROOT / "NOTICE", SITE_DIR / "NOTICE")
    (SITE_DIR / "source").mkdir()
    git = ["git", "-C", str(REPO_ROOT)]
    subprocess.run(
        [
            *git,
            "archive",
            "--format=tar.gz",
            "--output",
            str(SITE_DIR / "source" / "endury-source.tar.gz"),
            "HEAD",
        ],
        check=True,
    )
    return subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def main() -> None:
    """Build ``site/`` from ``web/``, ``endury/`` and Pyodide's release."""
    if not WEB_DIR.is_dir():
        sys.exit(f"error: {WEB_DIR} not found")
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)

    web_files = _files(WEB_DIR)
    build_id = _content_hash(web_files)
    _write_versioned_web(build_id, web_files)
    _fill_legal_pages(
        [
            root / page
            for root in (SITE_DIR, SITE_DIR / build_id)
            for page in LEGAL_PAGES
        ],
        LEGAL_DETAILS,
    )
    bundle_name = _build_python_bundle()
    pyodide_version = _stage_pyodide()
    commit = _stage_source()

    version = {
        "build": build_id,
        "commit": commit,
        "bundle": f"py/{bundle_name}",
        "pyodide": pyodide_version,
    }
    (SITE_DIR / "version.json").write_text(
        json.dumps(version, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"site/ built: build={build_id} bundle=py/{bundle_name} pyodide={pyodide_version}"
    )


if __name__ == "__main__":
    main()
