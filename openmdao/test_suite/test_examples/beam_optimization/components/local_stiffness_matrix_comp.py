from __future__ import division
import numpy as np
from openmdao.api import ExplicitComponent


class LocalStiffnessMatrixComp(ExplicitComponent):

    def initialize(self):
        self.metadata.declare('num_elements', types=int)
        self.metadata.declare('E')
        self.metadata.declare('L')

    def setup(self):
        num_elements = self.metadata['num_elements']
        E = self.metadata['E']
        L = self.metadata['L']

        self.add_input('I', shape=num_elements)
        self.add_output('K_local', shape=(num_elements, 4, 4))

        L0 = L / num_elements
        coeffs = np.empty((4, 4))
        coeffs[0, :] = [12 , 6 * L0, -12, 6 * L0]
        coeffs[1, :] = [6 * L0, 4 * L0 ** 2, -6 * L0, 2 * L0 ** 2]
        coeffs[2, :] = [-12 , -6 * L0, 12, -6 * L0]
        coeffs[3, :] = [6 * L0, 2 * L0 ** 2, -6 * L0, 4 * L0 ** 2]
        coeffs *= E / L0 ** 3

        self.mtx = mtx = np.zeros((num_elements, 4, 4, num_elements))
        for ind in range(num_elements):
            self.mtx[ind, :, :, ind] = coeffs

        self.declare_partials('K_local', 'I',
            val=self.mtx.reshape(16 * num_elements, num_elements))

    def compute(self, inputs, outputs):
        num_elements = self.metadata['num_elements']

        outputs['K_local'] = 0
        for ind in range(num_elements):
            outputs['K_local'][ind, :, :] = self.mtx[ind, :, :, ind] * inputs['I'][ind]
