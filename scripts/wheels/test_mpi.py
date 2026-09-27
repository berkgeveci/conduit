"""Two-rank check of the conduit-mpi add-on; run under mpiexec -n 2.

Imports mpi4py first (that is what selects the MPI ABI), then exercises
conduit.relay.mpi (communication), conduit.relay.mpi.io (parallel hdf5
write via a root-written file) and conduit.blueprint.mpi.
"""
import os
import sys

from mpi4py import MPI

comm = MPI.COMM_WORLD
rank, size = comm.Get_rank(), comm.Get_size()
comm_id = comm.py2f()


def log(*a):
    print(f"[{rank}]", *a, flush=True)


import numpy as np
import conduit
import conduit.relay.mpi as rmpi
import conduit.relay.mpi.io
import conduit.blueprint.mpi
import conduit.blueprint.mesh.examples as ex

log("conduit", conduit.about()["version"], "mpi module:",
    conduit.relay.mpi.__name__, "ranks:", rmpi.size(comm_id))
assert rmpi.rank(comm_id) == rank and rmpi.size(comm_id) == size

# all-reduce a node through relay.mpi
n = conduit.Node()
n.set(np.array([float(rank + 1)] * 4))
out = conduit.Node()
rmpi.sum_all_reduce(n, out, comm_id)
expected = sum(r + 1 for r in range(size))
assert np.allclose(out.value(), expected), (out.value(), expected)

# blueprint.mpi verify of a per-rank domain
mesh = conduit.Node()
ex.braid("uniform", 4, 4, 1, mesh)
mesh["state/domain_id"] = rank
info = conduit.Node()
assert conduit.blueprint.mpi.verify("mesh", mesh, info, comm_id), info.to_yaml()

# relay.mpi.io: broadcast a node, then a collective blueprint save/load
b = conduit.Node()
if rank == 0:
    b["hello"] = "world"
rmpi.broadcast_using_schema(b, 0, comm_id)
assert b["hello"] == "world"

mesh["state/cycle"] = 0
conduit.relay.mpi.io.blueprint.write_mesh(mesh, "conduit_mpi_test", comm_id, "hdf5")
back = conduit.Node()
conduit.relay.mpi.io.blueprint.read_mesh(back, "conduit_mpi_test.cycle_000000.root", comm_id)
assert back.number_of_children() >= 1
if rank == 0:
    log("mpi round trip ok; domains read on rank 0:", back.number_of_children(),
        "of", size, "(read_mesh distributes domains over ranks)")
