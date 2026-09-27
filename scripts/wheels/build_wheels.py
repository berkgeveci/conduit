#!/usr/bin/env python3
###############################################################################
# Build the "conduit-libs" and "conduit" wheels.
#
# Layout (site-packages):
#
#   conduit_libs/            <- py3-none-<platform> wheel "conduit-libs"
#     __init__.py              helper to locate lib/include/cmake dirs
#     lib/libconduit*.dylib    conduit shared libs (relative RPATHs)
#     lib/libhdf5*.dylib       bundled HDF5 (+ zlib)
#     lib/cmake/conduit/       relocatable CMake package
#     include/                 conduit + hdf5 + zlib headers
#     cmake/hdf5/              HDF5's own CMake package (used by ConduitConfig)
#     bin/                     relay utilities
#   conduit/                 <- cp3XX (or abi3) wheel "conduit"
#     ...                      python modules, RPATH -> ../conduit_libs/lib
#
# conduit_libs/ is a normal Conduit install prefix, so ConduitConfig.cmake
# works unchanged for C++ consumers (find_package(Conduit) with
# Conduit_DIR = <site-packages>/conduit_libs/lib/cmake/conduit).
#
# The Conduit build is configured with ENABLE_RELOCATABLE_INSTALL=ON, which
# gives the libs and python modules loader-relative RPATHs and records
# the bundled HDF5/zlib dirs relative to the prefix in ConduitConfig.cmake.
#
# Prototype status: macOS only. HDF5 and zlib must be pre-built with CMake
# (see scripts/build_conduit/build_conduit.sh), HDF5 as shared-only.
#
# Example:
#   python scripts/wheels/build_wheels.py \
#       --build-dir /path/to/build \
#       --hdf5-dir /path/to/tpls/install/hdf5-2.0.0 \
#       --zlib-dir /path/to/tpls/install/zlib-1.3.1 \
#       --python /path/to/venv/bin/python
###############################################################################

import argparse
import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

SYSTEM_LIB_PREFIXES = ("/usr/lib/", "/System/Library/")
RELATIVE_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")


def log(msg):
    print(f"[build_wheels] {msg}", flush=True)


def run(cmd, **kwargs):
    cmd = [str(c) for c in cmd]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.check_call(cmd, **kwargs)


def output(cmd):
    return subprocess.check_output([str(c) for c in cmd], text=True)


###############################################################################
# helpers
###############################################################################
def conduit_version(src_dir):
    txt = (src_dir / "CMakeLists.txt").read_text()
    m = re.search(r'project\(conduit\s+VERSION\s+"([^"]+)"', txt)
    if not m:
        raise RuntimeError("could not parse conduit version from CMakeLists.txt")
    return m.group(1)


def is_macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        magic = f.read(4)
    return magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa\xcf")


def macho_deps(path):
    """dependent libraries (install names) of a Mach-O file"""
    lines = output(["otool", "-L", path]).splitlines()[1:]
    deps = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        deps.append(line.split(" (compatibility")[0].strip())
    # for dylibs, the first entry is the lib's own install name
    if path.suffix == ".dylib" and deps:
        deps = deps[1:]
    return deps


def macho_id(path):
    out = output(["otool", "-D", path]).splitlines()
    return out[1].strip() if len(out) > 1 else None


def macho_rpaths(path):
    out = output(["otool", "-l", path]).splitlines()
    rpaths = []
    for i, line in enumerate(out):
        if line.strip() == "cmd LC_RPATH":
            for j in range(i, min(i + 4, len(out))):
                s = out[j].strip()
                if s.startswith("path "):
                    rpaths.append(s[len("path "):].split(" (offset")[0])
                    break
    return rpaths


def codesign(path):
    run(["codesign", "--force", "--sign", "-", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


###############################################################################
# staging
###############################################################################
def stage_tpls(hdf5_dir, zlib_dir, libs_pkg):
    """merge the HDF5 install and zlib runtime+headers into the libs package"""
    log(f"staging HDF5 from {hdf5_dir}")

    def ignore(_dir, names):
        return [n for n in names if n.endswith(".a") or n == "share"]

    shutil.copytree(hdf5_dir, libs_pkg, symlinks=True,
                    dirs_exist_ok=True, ignore=ignore)

    log(f"staging zlib from {zlib_dir}")
    (libs_pkg / "include").mkdir(parents=True, exist_ok=True)
    (libs_pkg / "lib").mkdir(parents=True, exist_ok=True)
    for h in (zlib_dir / "include").glob("*.h"):
        shutil.copy2(h, libs_pkg / "include" / h.name)
    for lib in (zlib_dir / "lib").glob("libz*.dylib"):
        dst = libs_pkg / "lib" / lib.name
        if lib.is_symlink():
            os.symlink(os.readlink(lib), dst)
        else:
            shutil.copy2(lib, dst)


def configure_build_install(args, src_dir, build_dir, staging, libs_pkg):
    cmake_args = [
        "cmake", "-S", src_dir, "-B", build_dir, "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=ON",
        "-DENABLE_RELOCATABLE_INSTALL=ON",
        f"-DCMAKE_INSTALL_PREFIX={libs_pkg}",
        f"-DPYTHON_MODULE_INSTALL_PREFIX={staging}",
        "-DENABLE_PYTHON=ON",
        f"-DPYTHON_EXECUTABLE={args.python}",
        f"-DCONDUIT_PYTHON_USE_LIMITED_API={'ON' if args.limited_api else 'OFF'}",
        f"-DHDF5_DIR={libs_pkg}",
        f"-DZLIB_DIR={libs_pkg}",
        "-DENABLE_TESTS=OFF",
        "-DENABLE_DOCS=OFF",
        "-DENABLE_EXAMPLES=OFF",
        "-DENABLE_UTILS=ON",
        "-DENABLE_FORTRAN=OFF",
        "-DENABLE_MPI=OFF",
        f"-DCMAKE_OSX_DEPLOYMENT_TARGET={args.deployment_target}",
    ]
    cmake_args += args.cmake_args
    run(cmake_args)
    run(["cmake", "--build", build_dir, "-j", str(args.jobs)])
    run(["cmake", "--install", build_dir])

    # conduit's cmake uses "pip install --target" to stage the pure python
    # sources, which leaves a dist-info behind that would conflict with
    # the one we generate for the wheel.
    for d in staging.glob("conduit-*.dist-info"):
        shutil.rmtree(d)


def write_libs_init(libs_pkg, version):
    (libs_pkg / "__init__.py").write_text(f'''"""Conduit runtime libraries, headers and CMake package.

This package is a relocatable Conduit install prefix. The ``conduit``
python package depends on it, and C++ projects can build against it:

    cmake -DConduit_DIR=$(python -m conduit_libs --cmake-dir) ...
"""
import os

__version__ = "{version}"

_root = os.path.dirname(os.path.abspath(__file__))


def root_dir():
    """the install prefix (lib/, include/, bin/, lib/cmake/conduit)"""
    return _root


def lib_dir():
    return os.path.join(_root, "lib")


def include_dir():
    return os.path.join(_root, "include")


def bin_dir():
    return os.path.join(_root, "bin")


def cmake_dir():
    """directory holding ConduitConfig.cmake (use as Conduit_DIR)"""
    return os.path.join(_root, "lib", "cmake", "conduit")


if os.name == "nt":  # pragma: no cover
    # dependents that load our DLLs by name need the directory registered
    os.add_dll_directory(bin_dir())
''')
    (libs_pkg / "__main__.py").write_text('''import sys

from . import bin_dir, cmake_dir, include_dir, lib_dir, root_dir

_opts = {
    "--root": root_dir,
    "--lib-dir": lib_dir,
    "--include-dir": include_dir,
    "--bin-dir": bin_dir,
    "--cmake-dir": cmake_dir,
}

args = sys.argv[1:] or ["--root"]
for a in args:
    if a not in _opts:
        sys.exit("usage: python -m conduit_libs [" + "|".join(_opts) + "]")
    print(_opts[a]())
''')


###############################################################################
# mach-o fixups
###############################################################################
def fix_macho(staging, libs_pkg):
    """
    Normalize install names, dependency references and RPATHs so that
    everything resolves relative to the loader:
      - dylibs in conduit_libs/lib get id @rpath/<name>, RPATH @loader_path
      - executables in conduit_libs/bin get RPATH @loader_path/../lib
      - python modules keep the relative RPATH set by CMake
      - references to bundled libs by absolute path become @rpath/<name>
      - absolute RPATH entries are removed
    """
    lib_dir = libs_pkg / "lib"
    bundled = {p.name for p in lib_dir.glob("*.dylib")}

    files = [p for p in staging.rglob("*") if is_macho(p)]
    problems = []
    for f in files:
        changed = False
        rel = f.relative_to(staging)

        if f.suffix == ".dylib" and f.parent == lib_dir:
            want_id = f"@rpath/{f.name}"
            if macho_id(f) != want_id:
                run(["install_name_tool", "-id", want_id, f])
                changed = True

        for dep in macho_deps(f):
            if dep.startswith(SYSTEM_LIB_PREFIXES):
                continue
            base = os.path.basename(dep)
            if dep.startswith(RELATIVE_PREFIXES):
                if base not in bundled:
                    problems.append(f"{rel}: unresolved {dep}")
                continue
            if base in bundled:
                run(["install_name_tool", "-change", dep, f"@rpath/{base}", f])
                changed = True
            else:
                problems.append(f"{rel}: external dependency {dep}")

        # rpaths
        rpaths = macho_rpaths(f)
        for rp in rpaths:
            if rp.startswith("/"):
                run(["install_name_tool", "-delete_rpath", rp, f])
                changed = True
        rpaths = [rp for rp in rpaths if not rp.startswith("/")]
        if f.parent == lib_dir:
            want = "@loader_path"
        elif f.parent == libs_pkg / "bin":
            want = "@loader_path/../lib"
        else:
            want = None
        if want and want not in rpaths:
            run(["install_name_tool", "-add_rpath", want, f])
            rpaths.append(want)
            changed = True
        if not rpaths and any(d.startswith("@rpath/") for d in macho_deps(f)):
            problems.append(f"{rel}: uses @rpath but has no RPATH")

        if changed:
            codesign(f)

    if problems:
        raise RuntimeError("mach-o fixups failed:\n  " + "\n  ".join(problems))
    log(f"fixed up {len(files)} mach-o files")


def check_resolution(staging, libs_pkg):
    """simulate the loader: every @rpath dep must resolve via the RPATHs"""
    problems = []
    for f in (p for p in staging.rglob("*") if is_macho(p)):
        rpaths = macho_rpaths(f)
        for dep in macho_deps(f):
            if not dep.startswith("@rpath/"):
                continue
            name = dep[len("@rpath/"):]
            found = False
            for rp in rpaths:
                if rp.startswith("@loader_path"):
                    d = (f.parent / rp[len("@loader_path/"):]).resolve() \
                        if rp != "@loader_path" else f.parent
                    if (d / name).exists():
                        found = True
                        break
            if not found:
                problems.append(f"{f.relative_to(staging)}: {dep} (rpaths: {rpaths})")
    if problems:
        raise RuntimeError("unresolved @rpath deps:\n  " + "\n  ".join(problems))
    log("all @rpath dependencies resolve relative to the loader")


###############################################################################
# wheel packing
###############################################################################
def urlsafe_b64(digest):
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def write_wheel(out_dir, name, version, tag, roots, metadata_extra, license_file):
    """
    Pack a wheel. `roots` is a list of (dir, arcname_prefix) whose contents
    are added; the dist-info is generated here.
    """
    dist = name.replace("-", "_")
    dist_info = f"{dist}-{version}.dist-info"
    whl = out_dir / f"{dist}-{version}-{tag}.whl"
    out_dir.mkdir(parents=True, exist_ok=True)
    if whl.exists():
        whl.unlink()

    metadata = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        "Summary: Simplified Data Exchange for HPC Simulations",
        "Home-page: https://github.com/llnl/conduit",
        "Author: Cyrus Harrison",
        "Author-email: cyrush@llnl.gov",
        "License: BSD-3-Clause",
        "Requires-Python: >=3.8",
    ] + metadata_extra
    py_tag, abi_tag, plat_tag = tag.split("-")
    wheel_meta = [
        "Wheel-Version: 1.0",
        "Generator: conduit build_wheels.py",
        f"Root-Is-Purelib: false",
        f"Tag: {tag}",
    ]

    records = []

    def add_bytes(zf, arcname, data):
        zf.writestr(arcname, data)
        digest = hashlib.sha256(data).digest()
        records.append(f"{arcname},sha256={urlsafe_b64(digest)},{len(data)}")

    def add_file(zf, path, arcname):
        if path.is_symlink():
            # zip has no portable symlinks; store a copy of the target
            path = path.resolve()
        data = path.read_bytes()
        info = zipfile.ZipInfo(arcname, date_time=(2016, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
        zf.writestr(info, data)
        digest = hashlib.sha256(data).digest()
        records.append(f"{arcname},sha256={urlsafe_b64(digest)},{len(data)}")

    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, prefix in roots:
            for p in sorted(root.rglob("*")):
                if p.is_dir() or "__pycache__" in p.parts:
                    continue
                arc = f"{prefix}/{p.relative_to(root).as_posix()}"
                add_file(zf, p, arc)
        add_bytes(zf, f"{dist_info}/METADATA", ("\n".join(metadata) + "\n").encode())
        add_bytes(zf, f"{dist_info}/WHEEL", ("\n".join(wheel_meta) + "\n").encode())
        if license_file and license_file.exists():
            add_bytes(zf, f"{dist_info}/LICENSE", license_file.read_bytes())
        records.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")
    log(f"wrote {whl} ({whl.stat().st_size / 1e6:.1f} MB)")
    return whl


def python_tags(python, limited_api):
    """(python tag, abi tag) for the interpreter the modules were built with"""
    if limited_api:
        # must match USE_SABI in src/cmake/thirdparty/SetupPython.cmake
        return "cp311", "abi3"
    out = output([python, "-c",
                  "import sysconfig,sys;"
                  "print(sys.version_info[0], sys.version_info[1],"
                  " sysconfig.get_config_var('abiflags') or '')"]).split()
    major, minor = out[0], out[1]
    abiflags = out[2] if len(out) > 2 else ""
    return f"cp{major}{minor}", f"cp{major}{minor}{abiflags}"


def platform_tag(deployment_target):
    major, minor = deployment_target.split(".")[:2]
    arch = platform.machine()
    if major != "10":
        minor = "0"
    return f"macosx_{major}_{minor}_{arch}"


###############################################################################
# main
###############################################################################
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src-dir", default=None,
                    help="conduit src dir (default: <repo>/src)")
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--hdf5-dir", required=True, type=Path,
                    help="HDF5 install prefix (CMake build, shared libs)")
    ap.add_argument("--zlib-dir", required=True, type=Path,
                    help="zlib install prefix")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--limited-api", action="store_true",
                    help="build python modules with the stable ABI (abi3)")
    ap.add_argument("--deployment-target", default="11.0")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse existing staging (only repack wheels)")
    ap.add_argument("cmake_args", nargs="*",
                    help="extra -D args passed to cmake")
    args = ap.parse_args()

    if sys.platform != "darwin":
        sys.exit("prototype: macOS only")

    repo = Path(__file__).resolve().parents[2]
    src_dir = Path(args.src_dir) if args.src_dir else repo / "src"
    build_root = Path(args.build_dir).resolve()
    build_dir = build_root / "conduit"
    staging = build_root / "staging"
    libs_pkg = staging / "conduit_libs"
    py_pkg = staging / "conduit"
    dist_dir = build_root / "dist"
    version = conduit_version(src_dir)
    log(f"conduit {version}, src {src_dir}")

    if not args.skip_build:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        stage_tpls(args.hdf5_dir.resolve(), args.zlib_dir.resolve(), libs_pkg)
        configure_build_install(args, src_dir, build_dir, staging, libs_pkg)
        write_libs_init(libs_pkg, version)
        fix_macho(staging, libs_pkg)

    check_resolution(staging, libs_pkg)

    plat = platform_tag(args.deployment_target)
    py_tag, abi_tag = python_tags(args.python, args.limited_api)
    license_file = repo / "LICENSE"

    libs_whl = write_wheel(
        dist_dir, "conduit-libs", version, f"py3-none-{plat}",
        [(libs_pkg, "conduit_libs")],
        ["Description: Conduit shared libraries, headers and CMake package "
         "(with bundled HDF5)."],
        license_file)
    py_whl = write_wheel(
        dist_dir, "conduit", version, f"{py_tag}-{abi_tag}-{plat}",
        [(py_pkg, "conduit")],
        [f"Requires-Dist: conduit-libs=={version}",
         "Requires-Dist: numpy"],
        license_file)

    log("done:")
    log(f"  {libs_whl}")
    log(f"  {py_whl}")


if __name__ == "__main__":
    main()
