from collections import OrderedDict

import unittest
from unittest.mock import patch
from unittest.mock import call
from qcodes.instrument.parameter import combine
from qcodes.utils.metadata import Metadatable
from qcodes.utils.helpers import full_class


class DumyPar(Metadatable):

    """Docstring for DumyPar. """

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.full_name = name

    def set(self, value):
        value = value * 2
        return value


class TestMultiPar(unittest.TestCase):

    def setUp(self):
        parameters = [DumyPar(name) for name in ["X", "Y", "Z"]]
        self.parameters = parameters
        self.input_dimensionality = len(parameters)

    def testCombine(self):
        multipar = combine(*self.parameters, name="combined")
        self.assertEqual(multipar.dimensionality,
                         self.input_dimensionality)

    def testSweepBadSetpoints(self):
        with self.assertRaises(ValueError):
            combine(*self.parameters, name="fail").sweep([[1, 2]])

    def testSweep(self):
        setpoints = []
        sweep_len = 2
        for _ in range(sweep_len):
            setpoints.append([1 for i in self.parameters])

        sweep_values = combine(*self.parameters,
                               name="combined").sweep(setpoints)

        res = []
        for i in sweep_values:
            value = sweep_values.set(i)
            res.append([i, value])
        expected = [
                [0, [1, 1, 1]],
                [1, [1, 1, 1]]
                ]
        self.assertEqual(res, expected)

    def testSet(self):
        setpoints = []
        sweep_len = 2
        for _ in range(sweep_len):
            setpoints.append([1 for i in self.parameters])

        sweep_values = combine(*self.parameters,
                               name="combined").sweep(setpoints)

        with patch.object(sweep_values, 'set') as mock_method:
            for i in sweep_values:
                    sweep_values.set(i)

        mock_method.assert_has_calls([
                    call(0), call(1)
                ]
            )

    def testAggregator(self):
        setpoints = []
        sweep_len = 2
        for _ in range(sweep_len):
            setpoints.append([1 for i in self.parameters])
        expected_results = [linear(*set) for set in setpoints]
        sweep_values = combine(*self.parameters,
                               name="combined",
                               aggregator=linear).sweep(setpoints)

        results = []
        for i, value in enumerate(sweep_values):
                res = sweep_values.set(value)
                results.append(sweep_values._aggregate(*res))

        self.assertEqual(results, expected_results)

    def testMeta(self):
        name = "combined"
        label = "Linear Combination"
        units = "a.u"
        aggregator = linear
        sweep_values = combine(*self.parameters,
                               name=name,
                               label=label,
                               units=units,
                               aggregator=aggregator
                               )
        snap = sweep_values.snapshot()
        out = OrderedDict()
        out['__class__'] = full_class(sweep_values)
        out["units"] = units
        out["label"] = label
        out["full_name"] = name
        out["aggreagator"] = repr(linear)
        for param in sweep_values.parameters:
            out[param.full_name] = {}
        self.assertEqual(out, snap)


def linear(x, y, z):
    return x+y+z
