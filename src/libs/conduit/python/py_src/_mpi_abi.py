# Copyright (c) Lawrence Livermore National Security, LLC and other Conduit
# Project developers. See top-level LICENSE AND COPYRIGHT files for dates and
# other details. No copyright assignment is required to contribute to Conduit.

###############################################################################
# file: _mpi_abi.py
# Purpose: Load the MPI-dependent compiled modules (conduit.relay.mpi, ...)
#          either from their usual place or, for installs that carry one
#          build per MPI ABI (conduit/relay/mpi/_mpich/, _openmpi/, ...),
#          from the variant matching the MPI library mpi4py has loaded.
#
# A variant install has no MPI library of its own: its libraries reference
# libmpi by soname only, and rely on the program having imported mpi4py.MPI
# first (which is how MPI python programs start). The ABI to use can also be
# forced with the CONDUIT_MPI_ABI environment variable (mpich, openmpi,
# mpiabi, impi, msmpi).
###############################################################################
import importlib
import os
import sys


def mpi_abi():
    """name of the MPI ABI mpi4py is using, or None"""
    forced = os.environ.get("CONDUIT_MPI_ABI", "").strip().lower()
    if forced:
        return forced
    if "mpi4py.MPI" not in sys.modules:
        return None
    try:
        from mpi4py import _mpiabi

        return _mpiabi._get_mpiabi()
    except Exception:
        pass
    try:
        from mpi4py import MPI

        version = MPI.Get_library_version()
    except Exception:
        return None
    if "Open MPI" in version:
        return "openmpi"
    if os.name == "nt":
        return "msmpi" if "Microsoft" in version else "impi"
    return "mpich"


def import_ext(package, name):
    """
    Import compiled module `name` for `package`: first as package.name (a
    regular install), then as package._<abi>.name (a variant install).
    Returns the public names of the module.
    """
    try:
        mod = importlib.import_module(f"{package}.{name}")
    except ImportError as plain_exc:
        abi = mpi_abi()
        if abi is None:
            raise ImportError(
                f"{package}: the MPI modules are provided per MPI ABI; import "
                "mpi4py.MPI before conduit's MPI modules (or set CONDUIT_MPI_ABI)"
            ) from plain_exc
        try:
            mod = importlib.import_module(f"{package}._{abi}.{name}")
        except ImportError as exc:
            raise ImportError(
                f"{package}: no MPI modules for the {abi} MPI ABI are installed "
                "(install the conduit-mpi wheel)"
            ) from exc
    return {k: v for k, v in vars(mod).items() if not k.startswith("_")}
