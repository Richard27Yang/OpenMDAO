"""Define the Group class."""
from __future__ import division

from six import iteritems, string_types
from collections import Iterable

import numpy as np

from openmdao.core.system import System, PathData
from openmdao.solvers.nl_bgs import NonlinearBlockGS
from openmdao.solvers.ln_bgs import LinearBlockGS
from openmdao.utils.general_utils import warn_deprecation


class Group(System):
    """Class used to group systems together; instantiate or inherit."""

    def __init__(self, **kwargs):
        """Set the solvers to nonlinear and linear block Gauss--Seidel by default."""
        super(Group, self).__init__(**kwargs)

        # TODO: we cannot set the solvers with property setters at the moment
        # because our lint check thinks that we are defining new attributes
        # called nl_solver and ln_solver without documenting them.
        if not self._nl_solver:
            self._nl_solver = NonlinearBlockGS()
            self._nl_solver._setup_solvers(self, 0)
        if not self._ln_solver:
            self._ln_solver = LinearBlockGS()
            self._ln_solver._setup_solvers(self, 0)

    def add(self, name, subsys, promotes=None):
        """Deprecated version of <Group.add_subsystem>.

        Args
        ----
        name : str
            Name of the subsystem being added
        subsys : System
            An instantiated, but not-yet-set up system object.
        promotes : iter of str, optional
            A list of variable names specifying which subsystem variables
            to 'promote' up to this group. This is for backwards compatibility
            with older versions of OpenMDAO.
        """
        warn_deprecation('This method provides backwards compabitibility with '
                         'OpenMDAO <= 1.x ; use add_subsystem instead.')

        self.add_subsystem(name, subsys, promotes=promotes)

    def add_subsystem(self, name, subsys, promotes=None,
                      promotes_inputs=None, promotes_outputs=None,
                      renames_inputs=None, renames_outputs=None):
        """Add a subsystem.

        Args
        ----
        name : str
            Name of the subsystem being added
        subsys : <System>
            An instantiated, but not-yet-set up system object.
        promotes : str, iter of str, optional
            One or a list of variable names specifying which subsystem variables
            to 'promote' up to this group. This is for backwards compatibility
            with older versions of OpenMDAO.
        promotes_inputs : str, iter of str, optional
            One or a list of input variable names specifying which subsystem input
            variables to 'promote' up to this group.
        promotes_outputs : str, iter of str, optional
            One or a list of output variable names specifying which subsystem output
            variables to 'promote' up to this group.
        renames_inputs : list of (str, str) or dict, optional
            A dict mapping old name to new name for any subsystem
            input variables that should be renamed in this group.
        renames_outputs : list of (str, str) or dict, optional
            A dict mapping old name to new name for any subsystem
            output variables that should be renamed in this group.

        Returns
        -------
        <System>
            the subsystem that was passed in. This is returned to
            enable users to instantiate and add a subsystem at the
            same time, and get the pointer back.

        """
        for sub in self._subsystems_allprocs:
            if name == sub.name:
                raise RuntimeError("Subsystem name '%s' is already used." %
                                   name)

        self._subsystems_allprocs.append(subsys)
        subsys.name = name

        if promotes:
            subsys._var_promotes['any'] = set(promotes)
        if promotes_inputs:
            subsys._var_promotes['input'] = set(promotes_inputs)
        if promotes_outputs:
            subsys._var_promotes['output'] = set(promotes_outputs)
        if renames_inputs:
            subsys._var_renames['input'] = dict(renames_inputs)
        if renames_outputs:
            subsys._var_renames['output'] = dict(renames_outputs)

        return subsys

    def connect(self, out_name, in_name, src_indices=None):
        """Connect output out_name to input in_name in this namespace.

        Args
        ----
        out_name : str
            name of the output (source) variable to connect
        in_name : str or [str, ... ] or (str, ...)
            name of the input or inputs (target) variable to connect
        src_indices : collection of int optional
            When an input variable connects to some subset of an array output
            variable, you can specify which indices of the source to be
            transferred to the input here.
        """
        # if src_indices argument is given, it should be valid
        if isinstance(src_indices, string_types):
            if isinstance(in_name, string_types):
                in_name = [in_name]
            in_name.append(src_indices)
            raise TypeError("src_indices must be an index array, did you mean"
                            " connect('%s', %s)?" % (out_name, in_name))

        if isinstance(src_indices, np.ndarray):
            if not np.issubdtype(src_indices.dtype, np.integer):
                raise TypeError("src_indices must contain integers, but src_indices for "
                                "connection from '%s' to '%s' is %s." %
                                (out_name, in_name, src_indices.dtype.type))
        elif isinstance(src_indices, Iterable):
            types_in_src_idxs = set(type(idx) for idx in src_indices)
            for t in types_in_src_idxs:
                if not np.issubdtype(t, np.integer):
                    raise TypeError("src_indices must contain integers, but src_indices for "
                                    "connection from '%s' to '%s' contains non-integers." %
                                    (out_name, in_name))

        # if multiple targets are given, recursively connect to each
        if isinstance(in_name, (list, tuple)):
            for name in in_name:
                self.connect(out_name, name, src_indices)
            return

        # target should not already be connected
        if in_name in self._var_connections:
            srcname = self._var_connections[in_name][0]
            raise RuntimeError("Input '%s' is already connected to '%s'." %
                               (in_name, srcname))

        if out_name.rsplit('.', 1)[0] == in_name.rsplit('.', 1)[0]:
            raise RuntimeError("Input and output are in the same System for " +
                               "connection from '%s' to '%s'." % (out_name, in_name))

        self._var_connections[in_name] = (out_name, src_indices)

    def _setup_connections(self):
        """Recursively assemble a list of input-output connections.

        Sets the following attributes:
            _var_connections_indices
        """
        # Perform recursion and assemble pairs from subsystems
        pairs = []
        for subsys in self._subsystems_myproc:
            subsys._setup_connections()
            if subsys.comm.rank == 0:
                pairs.extend(subsys._var_connections_indices)

        # Do an allgather to gather from root procs of all subsystems
        if self.comm.size > 1:
            pairs_raw = self.comm.allgather(pairs)
            pairs = []
            for sub_pairs in pairs_raw:
                pairs.extend(sub_pairs)

        allprocs_in_names = self._var_allprocs_names['input']
        myproc_in_names = self._var_myproc_names['input']
        allprocs_out_names = self._var_allprocs_names['output']
        input_meta = self._var_myproc_metadata['input']

        in_offset = self._var_allprocs_range['input'][0]
        out_offset = self._var_allprocs_range['output'][0]

        # Loop through user-defined connections
        for in_name, (out_name, src_indices) \
                in iteritems(self._var_connections):

            # throw an exception if either output or input doesn't exist
            # (not traceable to a connect statement, so provide context)
            if out_name not in allprocs_out_names:
                raise NameError("Output '%s' does not exist for connection "
                                "in '%s' from '%s' to '%s'." %
                                (out_name, self.name if self.name else 'model',
                                 out_name, in_name))

            if in_name not in allprocs_in_names:
                raise NameError("Input '%s' does not exist for connection "
                                "in '%s' from '%s' to '%s'." %
                                (in_name, self.name if self.name else 'model',
                                 out_name, in_name))

            # throw an exception if output and input are in the same system
            # (not traceable to a connect statement, so provide context)
            out_subsys = out_name.rsplit('.', 1)[0] if '.' in out_name \
                else self._find_subsys_with_promoted_name(out_name, 'output')

            in_subsys = in_name.rsplit('.', 1)[0] if '.' in in_name \
                else self._find_subsys_with_promoted_name(in_name, 'input')

            if out_subsys == in_subsys:
                raise RuntimeError("Input and output are in the same System " +
                                   "for connection in '%s' from '%s' to '%s'." %
                                   (self.name if self.name else 'model',
                                    out_name, in_name))

            for in_index, name in enumerate(allprocs_in_names):
                if name == in_name:
                    try:
                        out_index = allprocs_out_names.index(out_name)
                    except ValueError:
                        continue
                    else:
                        pairs.append((in_index + in_offset,
                                      out_index + out_offset))

                    if src_indices is not None:
                        # set the 'indices' metadata in the input variable
                        try:
                            in_myproc_index = myproc_in_names.index(in_name)
                        except ValueError:
                            pass
                        else:
                            meta = input_meta[in_myproc_index]
                            meta['indices'] = np.array(src_indices, dtype=int)

                        # set src_indices to None to avoid unnecessary repeat
                        # of setting indices and shape metadata when we have
                        # multiple inputs promoted to the same name.
                        src_indices = None

        self._var_connections_indices = pairs

    def _find_subsys_with_promoted_name(self, var_name, io_type='output'):
        """Find subsystem that contains promoted variable.

        Args
        ----
        var_name : str
            variable name
        io_type : str
            'output' or 'input'.

        Returns
        -------
        str
            name of subsystem, None if not found.
        """
        for subsys in self._subsystems_allprocs:
            for name, prom_name in iteritems(subsys._var_maps[io_type]):
                if var_name == prom_name:
                    return subsys.name
        return None

    def initialize_variables(self):
        """Set up variable name and metadata lists."""
        self._var_pathdict = {}
        self._var_name2path = {}

        for typ in ['input', 'output']:
            for subsys in self._subsystems_myproc:
                # Assemble the names list from subsystems
                subsys._var_maps[typ] = subsys._get_maps(typ)
                paths = subsys._var_allprocs_pathnames[typ]
                for idx, subname in enumerate(subsys._var_allprocs_names[typ]):
                    name = subsys._var_maps[typ][subname]
                    self._var_allprocs_names[typ].append(name)
                    self._var_allprocs_pathnames[typ].append(paths[idx])
                    self._var_myproc_names[typ].append(name)

                # Assemble the metadata list from the subsystems
                metadata = subsys._var_myproc_metadata[typ]
                self._var_myproc_metadata[typ].extend(metadata)

            # The names list is on all procs, allgather all names
            if self.comm.size > 1:

                # One representative proc from each sub_comm adds names
                sub_comm = self._subsystems_myproc[0].comm
                if sub_comm.rank == 0:
                    names = (self._var_allprocs_names[typ],
                             self._var_allprocs_pathnames[typ])
                else:
                    names = ([], [])

                # Every proc on this comm now has global variable names
                self._var_allprocs_names[typ] = []
                self._var_allprocs_pathnames[typ] = []
                for names, pathnames in self.comm.allgather(names):
                    self._var_allprocs_names[typ].extend(names)
                    self._var_allprocs_pathnames[typ].extend(pathnames)

            for idx, name in enumerate(self._var_allprocs_names[typ]):
                path = self._var_allprocs_pathnames[typ][idx]
                self._var_pathdict[path] = PathData(name, idx, typ)
                if name in self._var_name2path:
                    self._var_name2path[name].append(path)
                else:
                    self._var_name2path[name] = [path]

    def _apply_nonlinear(self):
        """Compute residuals."""
        self._transfers[None](self._inputs, self._outputs, 'fwd')
        # Apply recursion
        for subsys in self._subsystems_myproc:
            subsys._apply_nonlinear()

    def _solve_nonlinear(self):
        """Compute outputs.

        Returns
        -------
        boolean
            Failure flag; True if failed to converge, False is successful.
        float
            relative error.
        float
            absolute error.
        """
        return self._nl_solver.solve()

    def _apply_linear(self, vec_names, mode, var_inds=None):
        """Compute jac-vec product.

        Args
        ----
        vec_names : [str, ...]
            list of names of the right-hand-side vectors.
        mode : str
            'fwd' or 'rev'.
        var_inds : [int, int, int, int] or None
            ranges of variable IDs involved in this matrix-vector product.
            The ordering is [lb1, ub1, lb2, ub2].
        """
        # Use global Jacobian
        if self._jacobian._top_name == self.pathname:
            for vec_name in vec_names:
                with self._matvec_context(vec_name, var_inds, mode) as vecs:
                    d_inputs, d_outputs, d_residuals = vecs
                    self._jacobian._system = self
                    self._jacobian._apply(d_inputs, d_outputs, d_residuals,
                                          mode)
        # Apply recursion
        else:
            if mode == 'fwd':
                for vec_name in vec_names:
                    d_inputs = self._vectors['input'][vec_name]
                    d_outputs = self._vectors['output'][vec_name]
                    self._vector_transfers[vec_name][None](
                        d_inputs, d_outputs, mode)

            for subsys in self._subsystems_myproc:
                subsys._apply_linear(vec_names, mode, var_inds)

            if mode == 'rev':
                for vec_name in vec_names:
                    d_inputs = self._vectors['input'][vec_name]
                    d_outputs = self._vectors['output'][vec_name]
                    self._vector_transfers[vec_name][None](
                        d_inputs, d_outputs, mode)

    def _solve_linear(self, vec_names, mode):
        """Apply inverse jac product.

        Args
        ----
        vec_names : [str, ...]
            list of names of the right-hand-side vectors.
        mode : str
            'fwd' or 'rev'.

        Returns
        -------
        boolean
            Failure flag; True if failed to converge, False is successful.
        float
            relative error.
        float
            absolute error.
        """
        return self._ln_solver.solve(vec_names, mode)

    def _linearize(self):
        """Compute jacobian / factorization."""
        for subsys in self._subsystems_myproc:
            subsys._linearize()

        # Update jacobian
        if self._jacobian._top_name == self.pathname:
            self._jacobian._system = self
            self._jacobian._update()
