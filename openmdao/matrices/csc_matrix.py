"""Define the CSCmatrix class."""
from __future__ import print_function

import numpy as np
from scipy.sparse import coo_matrix
from six import iteritems

from openmdao.matrices.coo_matrix import COOMatrix


class CSCMatrix(COOMatrix):
    """
    Sparse matrix in Compressed Col Storage format.
    """

    def _build(self, num_rows, num_cols):
        """
        Allocate the matrix.

        Parameters
        ----------
        num_rows : int
            number of rows in the matrix.
        num_cols : int
            number of cols in the matrix.
        """
        data, rows, cols = self._build_sparse(num_rows, num_cols)

        # get a set of indices that sorts into col major order
        srtidxs = np.lexsort((rows, cols))

        data = data[srtidxs]
        rows = rows[srtidxs]
        cols = cols[srtidxs]

        # now sort these back into ascending order (our original stacked order)
        # so in _update_submat() we can just extract the individual index
        # arrays that will map each block into the combined data array.
        revidxs = np.argsort(srtidxs)

        metadata = self._metadata
        for key, (ind1, ind2, idxs, jac_type, factor) in iteritems(metadata):
            if idxs is None:
                metadata[key] = (revidxs[ind1:ind2], jac_type, factor)
            else:
                # apply the reverse index to each part of revidxs so that
                # we can avoid copying the index array during updates.
                metadata[key] = (revidxs[ind1:ind2][np.argsort(idxs)],
                                 jac_type, factor)

        # data array for the CSC should be the same as for the COO since
        # it was already in sorted order.
        coo = coo_matrix((data, (rows, cols)), shape=(num_rows, num_cols))
        coo_data_size = coo.data.size
        self._matrix = coo.tocsc()

        # make sure data size is the same between coo and csr, else indexing is
        # messed up
        if coo_data_size != self._matrix.data.size:
            raise ValueError("CSC matrix data contains duplicate row/col entries. "
                             "This would break internal indexing.")
