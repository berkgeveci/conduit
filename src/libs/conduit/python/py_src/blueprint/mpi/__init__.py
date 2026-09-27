# Copyright (c) Lawrence Livermore National Security, LLC and other Conduit
# Project developers. See top-level LICENSE AND COPYRIGHT files for dates and
# other details. No copyright assignment is required to contribute to Conduit.

###############################################################################
# file: __init__.py
# Purpose: Main init for the conduit blueprint mpi module.
###############################################################################
from conduit._mpi_abi import import_ext as _import_ext

globals().update(_import_ext(__name__, "conduit_blueprint_mpi_python"))
