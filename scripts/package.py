#!/usr/bin/env python3
"""Read-only integrity checks and reproducible local ZIPs; Python >= 3.9/POSIX."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile


NAME = "architecture-diagram"
MANIFEST = "manifest.json"
DIAGNOSTIC_LIMIT = 2000
CHUNK_SIZE = 1024 * 1024
NOTICE = "Local integrity only; not a signature or proof of trusted provenance."


class PackageError(Exception):
    """An invalid package, invocation, or output destination."""


def _diagnostic(message):
    message = str(message)
    if len(message) <= DIAGNOSTIC_LIMIT:
        return message
    suffix = " [truncated; original characters: %d]" % len(message)
    return message[:DIAGNOSTIC_LIMIT - len(suffix)] + suffix


def _result(command=None):
    return {"ok": False, "command": command, "version": None,
            "distribution": None, "sha256": None, "hash_scope": None,
            "bytes": None, "files": None, "diagnostic": ""}


def _emit(result):
    result["diagnostic"] = _diagnostic(result["diagnostic"])
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PackageError("arguments: " + message)

    def print_help(self, file=None):
        result = _result()
        result.update(ok=True, diagnostic=self.format_help())
        _emit(result)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PackageError("duplicate JSON key: %r" % key)
        obj[key] = value
    return obj


def _invalid_constant(value):
    raise PackageError("invalid JSON constant: " + value)


def _path(value, inventory=False):
    if not isinstance(value, str) or not value:
        raise PackageError("inventory path must be a nonempty string")
    parts = value.split("/")
    if (any(part in ("", ".", "..") for part in parts)
            or any(char in value for char in "\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part.endswith((".", " ")) for part in parts)):
        raise PackageError("unsafe relative path: %r" % value)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PackageError("path must be valid UTF-8: %r" % value) from None
    if inventory and any(part in (".venv", "__pycache__", ".DS_Store")
                         for part in parts):
        raise PackageError("excluded runtime/cache path in inventory: %r" % value)
    if parts[-1].casefold() == "skill.md" and value != "SKILL.md":
        raise PackageError("duplicate/non-root SKILL.md entry: %r" % value)
    return parts


class Source:
    """Use POSIX directory descriptors to reject replaced ancestors and symlinks."""

    def __init__(self, root):
        root = Path(root).absolute()
        if stat.S_ISLNK(root.lstat().st_mode):
            raise PackageError("package root must not be a symlink")
        self.root = root.resolve(strict=True)
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return self

    def __exit__(self, *args):
        os.close(self.fd)

    def open_file(self, relative):
        parts = _path(relative)
        directory = os.dup(self.fd)
        descriptor = None
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise PackageError("not a regular file: %r" % relative)
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            return stream
        finally:
            os.close(directory)
            if descriptor is not None:
                os.close(descriptor)

    def scan(self):
        files = set()

        def visit(directory, prefix="", ignored=False):
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
            for entry in entries:
                relative = prefix + entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise PackageError("symlink is forbidden: %r" % relative)
                _path(relative)
                if entry.name == ".venv":
                    if prefix or not stat.S_ISDIR(mode):
                        raise PackageError("only a real root .venv directory is excluded")
                    continue
                if stat.S_ISDIR(mode):
                    if entry.name == ".DS_Store":
                        raise PackageError(".DS_Store must be a regular file")
                    child = os.open(entry.name,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory)
                    try:
                        visit(child, relative + "/", ignored or entry.name == "__pycache__")
                    finally:
                        os.close(child)
                elif stat.S_ISREG(mode):
                    if entry.name == "__pycache__":
                        raise PackageError("__pycache__ must be a directory")
                    if not ignored and entry.name != ".DS_Store":
                        files.add(relative)
                else:
                    raise PackageError("not a regular file/directory: %r" % relative)

        visit(self.fd)
        return files


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _consume(source, relative, expected=None, destination=None):
    digest = hashlib.sha256()
    size = 0
    with source.open_file(relative) as stream:
        before = os.fstat(stream.fileno())
        if expected is not None and before.st_size != expected["bytes"]:
            raise PackageError("bytes mismatch: %r" % relative)
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if expected is not None and size > expected["bytes"]:
                raise PackageError("bytes changed during read: %r" % relative)
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        if _fingerprint(before) != _fingerprint(os.fstat(stream.fileno())):
            raise PackageError("file changed during read: %r" % relative)
    metadata = {"sha256": digest.hexdigest(), "bytes": size}
    if expected is not None:
        if size != expected["bytes"]:
            raise PackageError("bytes mismatch: %r" % relative)
        if metadata["sha256"] != expected["sha256"].lower():
            raise PackageError("sha256 mismatch: %r" % relative)
    return metadata


def _load_manifest(source, result):
    with source.open_file(MANIFEST) as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read()
        if _fingerprint(before) != _fingerprint(os.fstat(stream.fileno())):
            raise PackageError("manifest changed during read")
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    if not isinstance(manifest, dict):
        raise PackageError("manifest must be a JSON object")
    for key in ("version", "distribution"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PackageError("manifest.%s must be a nonempty string" % key)
        result[key] = value
    if manifest.get("skill") != NAME:
        raise PackageError("manifest.skill must be architecture-diagram")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("files"), dict):
        raise PackageError("manifest.inventory.files must be an object")
    files = inventory["files"]
    seen = {MANIFEST.casefold()}
    for relative, metadata in files.items():
        _path(relative, inventory=True)
        if relative == MANIFEST:
            raise PackageError("inventory must exclude manifest.json itself")
        if relative.casefold() in seen:
            raise PackageError("case-colliding inventory path: %r" % relative)
        seen.add(relative.casefold())
        if not isinstance(metadata, dict):
            raise PackageError("invalid file metadata: %r" % relative)
        sha256, size = metadata.get("sha256"), metadata.get("bytes")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise PackageError("invalid sha256: %r" % relative)
        if type(size) is not int or size < 0:
            raise PackageError("invalid bytes: %r" % relative)
    if "SKILL.md" not in files:
        raise PackageError("inventory requires the unique root SKILL.md")
    records = dict(files)
    records[MANIFEST] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return records


def _summary(paths):
    paths = sorted(paths)
    text = ", ".join(repr(path) for path in paths[:10])
    if len(paths) > 10:
        text += " [truncated; %d more paths omitted]" % (len(paths) - 10)
    return text


def _check_set(source, records):
    actual, expected = source.scan(), set(records)
    missing, extra = expected - actual, actual - expected
    if missing or extra:
        diagnostic = []
        if missing:
            diagnostic.append("missing files: " + _summary(missing))
        if extra:
            diagnostic.append("unregistered files: " + _summary(extra))
        raise PackageError("; ".join(diagnostic))


def verify(source, result):
    """Validate without mutations; return the records needed for a build."""
    records = _load_manifest(source, result)
    _check_set(source, records)
    for relative in sorted(records):
        _consume(source, relative, records[relative])
    result.update(sha256=records[MANIFEST]["sha256"], hash_scope=MANIFEST,
                  bytes=sum(record["bytes"] for record in records.values()),
                  files=len(records))
    return records


def _output_path(source, output):
    output = Path(output)
    if not output.is_absolute():
        raise PackageError("output must be an absolute path")
    if os.path.lexists(output):
        raise PackageError("output already exists; refusing overwrite")
    parent = output.parent.resolve(strict=True)
    if not parent.is_dir():
        raise PackageError("output parent must be an existing directory")
    resolved = parent / output.name
    if resolved == source.root or source.root in resolved.parents:
        raise PackageError("output must be outside the source package")
    return resolved


def build(source, records, output, result):
    """Use ZIP_STORED for zlib-independent bytes; remove only our partial file on failure."""
    output = _output_path(source, output)
    created = None
    try:
        with output.open("x+b") as stream:
            created = os.fstat(stream.fileno())
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED,
                                 allowZip64=True) as archive:
                for relative in sorted(records):
                    info = zipfile.ZipInfo(NAME + "/" + relative, (1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_STORED
                    info.file_size = records[relative]["bytes"]
                    with archive.open(info, "w") as member:
                        _consume(source, relative, records[relative], member)
            # Refuse a changed manifest or new/unregistered members during build.
            _check_set(source, records)
            _consume(source, MANIFEST, records[MANIFEST])
            stream.flush()
            os.fsync(stream.fileno())
            size = stream.tell()
            stream.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        result.update(sha256=digest.hexdigest(), hash_scope="archive", bytes=size,
                      files=len(records), output=str(output))
    except BaseException:
        if created is not None:
            try:
                current = output.lstat()
                if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    output.unlink()
            except FileNotFoundError:
                pass
        raise


def main(argv=None):
    result = _result()
    try:
        parser = Parser(description="Verify or build a local skill package. " + NOTICE)
        commands = parser.add_subparsers(dest="command", required=True)
        for command in ("verify", "build"):
            subparser = commands.add_parser(command)
            subparser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
            if command == "build":
                subparser.add_argument("--output", required=True)
        args = parser.parse_args(argv)
        result["command"] = args.command
        with Source(args.root) as source:
            records = verify(source, result)
            if args.command == "build":
                build(source, records, args.output, result)
        result.update(ok=True, diagnostic="%s passed. %s" % (args.command, NOTICE))
        status = 0
    except (Exception, KeyboardInterrupt) as error:
        # Do not mislabel a successful pre-build manifest digest as an artifact.
        result.update(sha256=None, hash_scope=None, bytes=None, files=None,
                      diagnostic="%s: %s" % (type(error).__name__, error))
        status = 1
    _emit(result)
    return status


if __name__ == "__main__":
    sys.exit(main())
