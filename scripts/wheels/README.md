# Conduit wheels (prototype)

`build_wheels.py` builds two wheels from a Conduit checkout:

| wheel          | tag                    | contents                                                        |
|----------------|------------------------|-----------------------------------------------------------------|
| `conduit-libs` | `py3-none-<platform>`  | `conduit_libs/`: shared libs, bundled HDF5 + zlib, headers, CMake package, relay utilities |
| `conduit`      | `cp3XX-cp3XX-<platform>` (or `cp311-abi3` with `--limited-api`) | `conduit/`: the python modules; depends on `conduit-libs==<version>` |

`conduit_libs/` is a normal, relocatable Conduit install prefix that happens to
live inside site-packages. Nothing is installed into the interpreter prefix.

## Using the wheels

Python:

```python
import conduit, conduit.relay.io
```

C++ / CMake (VTK, Catalyst, Fides, ...):

```bash
cmake -DConduit_DIR=$(python -m conduit_libs --cmake-dir) ...
```

`find_package(Conduit)` then resolves the bundled HDF5 and zlib relative to
the package (`CONDUIT_HDF5_DIR`/`CONDUIT_ZLIB_DIR` are recorded as
`${_IMPORT_ROOT}` in `ConduitConfig.cmake`), and consumers link
`conduit::conduit` as usual. Their binaries need an RPATH that reaches
`conduit_libs/lib`; CMake adds it automatically for build-tree binaries,
and a wheel consumer would use `@loader_path`/`$ORIGIN` relative to its own
package (for example `@loader_path/../../conduit_libs/lib`).

## How it works

* Conduit is configured with `ENABLE_RELOCATABLE_INSTALL=ON`, which
  * gives installed libraries and executables loader-relative RPATHs
    (`@loader_path`, `@loader_path/../lib` on macOS; `$ORIGIN` variants on
    Linux) and `@rpath` install names,
  * points each python module's RPATH at `<prefix>/lib` relative to where
    the module is installed,
  * records third-party install dirs that live inside the install prefix
    relative to the prefix in the installed CMake config,
  * keeps python modules out of the CMake export set when they are
    installed outside the prefix (their absolute paths would otherwise
    break `find_package(Conduit)` on another machine).
* HDF5 (shared-only CMake build) and zlib are copied into the staging
  prefix first, and Conduit is built against that copy so the recorded
  paths are already inside the prefix.
* After `cmake --install`, install names, dependency references and RPATHs
  of all Mach-O files are normalized and re-signed, and the script checks
  that every `@rpath/` reference resolves relative to its loader.
* The wheels are packed directly (METADATA / WHEEL / RECORD), no
  setuptools involved.

## Building (macOS)

Build zlib and HDF5 first, e.g. with the TPL script, then rebuild HDF5
shared-only without tools (its CMake package must not reference files we
don't ship):

```bash
env prefix=$TPLS build_macos=ON build_conduit=false build_cgns=false \
    build_pyvenv=false build_camp=false build_raja=false build_umpire=false \
    build_silo=false enable_python=OFF \
    bash scripts/build_conduit/build_conduit.sh

cmake -S $TPLS/source/hdf5-2.0.0 -B $TPLS/build/hdf5-2.0.0 \
    -DBUILD_STATIC_LIBS=OFF -DHDF5_BUILD_TOOLS=OFF -DHDF5_BUILD_UTILS=OFF \
    -DHDF5_BUILD_HL_LIB=OFF -DHDF5_BUILD_CPP_LIB=OFF -DBUILD_TESTING=OFF \
    -DHDF5_ENABLE_ZLIB_SUPPORT=ON -DCMAKE_PREFIX_PATH=$TPLS/install/zlib-1.3.1 \
    -DCMAKE_INSTALL_PREFIX=$TPLS/install/hdf5-2.0.0
cmake --build $TPLS/build/hdf5-2.0.0 && cmake --install $TPLS/build/hdf5-2.0.0
```

Then, from a venv that has `numpy` and `pip`:

```bash
python scripts/wheels/build_wheels.py \
    --build-dir /path/to/build \
    --hdf5-dir $TPLS/install/hdf5-2.0.0 \
    --zlib-dir $TPLS/install/zlib-1.3.1 \
    --python $(which python) \
    --deployment-target 11.0
```

Wheels land in `<build-dir>/dist/`. Use `--limited-api` to build the
python modules against the stable ABI (one `cp311-abi3` `conduit` wheel per
platform that works on Python 3.11 and newer, instead of one per Python
version). This sets `CONDUIT_PYTHON_USE_LIMITED_API=ON`, which requires
CMake's `Development.SABIModule` component and `USE_SABI 3.11`; both are
handled in `src/cmake/thirdparty/SetupPython.cmake`.

## MPI add-on wheels

`--mpi ABI=PYTHON` (repeatable) builds one MPI variant of Conduit per MPI
ABI and packs two add-on wheels:

| wheel              | tag                   | contents |
|--------------------|-----------------------|----------|
| `conduit-libs-mpi` | `py3-none-<platform>` | `conduit_libs/mpi/<abi>/lib/libconduit_{relay_mpi,relay_mpi_io,blueprint_mpi}.dylib`, `conduit_libs/lib/cmake/conduit_mpi_<abi>/` (a full Conduit CMake package), the MPI headers |
| `conduit-mpi`      | same as `conduit`     | `conduit/**/_<abi>/*.so` (the six MPI python modules per ABI) |

Each variant is a Conduit build with `ENABLE_MPI=ON`, `MPI_C_COMPILER`
from a venv holding the `mpich` or `openmpi` wheel from PyPI, and
`CONDUIT_INSTALL_MPI_VARIANT=<abi>`, which makes the MPI libraries install
to `mpi/<abi>/lib` (RPATH `@loader_path`, `@loader_path/../../../lib` for the
serial libs, HDF5 and zlib), the CMake package to `lib/cmake/conduit_mpi_<abi>`
(its serial targets point at `lib/`, its MPI targets at `mpi/<abi>/lib`), and
the MPI python modules to `_<abi>` subpackages. Only those pieces are kept;
the serial libraries, headers and modules come from the base wheels, which
the add-ons pin to the exact version. `libmpi` is referenced by soname only
and never bundled.

At import time `conduit/_mpi_abi.py` loads the MPI modules from the usual
place (a regular install) or, failing that, from the `_<abi>` subpackage
matching the MPI library mpi4py has loaded — so MPI programs import
`mpi4py.MPI` first, as they normally do (`CONDUIT_MPI_ABI` overrides). With
no mpi4py loaded, `conduit.relay.mpi` raises an `ImportError` that says so,
and `import conduit` keeps working as before.

C++ consumers of an MPI variant:

```bash
cmake -DConduit_DIR=<site-packages>/conduit_libs/lib/cmake/conduit_mpi_<abi> \
      -DMPI_C_COMPILER=<venv>/bin/mpicc -DMPI_CXX_COMPILER=<venv>/bin/mpicxx ...
find_package(Conduit REQUIRED COMPONENTS MPI)   # conduit::conduit_mpi
```

`test_mpi.py` (run under `mpiexec -n 2`) exercises `relay.mpi`,
`blueprint.mpi` and `relay.mpi.io.blueprint` through the add-on.

Verified on macOS arm64 with the `mpich` and `openmpi` wheels: 2-rank python
round trips through both variants, and a C++ consumer via
`find_package(Conduit COMPONENTS MPI)` against the openmpi variant (the
`mpich` 5.0.1 wheel hangs in `MPI_Finalize` for C programs on this Mac).

## Not done yet

* Linux (`auditwheel`-style handling with `$ORIGIN`) and Windows
  (`delvewheel`, DLL name mangling, `os.add_dll_directory` in `conduit`).
* A standard-MPI-ABI (`libmpi_abi`) variant.
* `ConduitConfig.cmake` still records the build-time python executable and
  python module dir; harmless for the wheel but not meaningful.
* Moving from this script to scikit-build-core + cibuildwheel for CI.
