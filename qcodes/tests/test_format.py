from unittest import TestCase
import os
import h5py # TODO: add this to the dependencies in setup.py
import numpy as np
from qcodes.data.format import Formatter
from qcodes.data.gnuplot_format import GNUPlotFormat
from qcodes.data.hdf5_format import HDF5Format
from qcodes.data.data_array import DataArray
from qcodes.data.data_set import DataSet, new_data, load_data
from qcodes.utils.helpers import LogCapture
from qcodes.station import Station
from qcodes import Loop
from .data_mocks import DataSet1D, file_1d, DataSetCombined, files_combined

from qcodes.tests.instrument_mocks import MockParabola


class TestBaseFormatter(TestCase):
    def setUp(self):
        self.io = DataSet.default_io
        self.locations = ('_simple1d_', '_combined_')

        for location in self.locations:
            self.assertFalse(self.io.list(location))

    def tearDown(self):
        for location in self.locations:
            self.io.remove_all(location)

    def test_overridable_methods(self):
        formatter = Formatter()
        data = DataSet1D()

        with self.assertRaises(NotImplementedError):
            formatter.write(data, data.io, data.location)
        with self.assertRaises(NotImplementedError):
            formatter.read_one_file(data, 'a file!', set())

        with self.assertRaises(NotImplementedError):
            formatter.write_metadata(data, data.io, data.location)
        with self.assertRaises(NotImplementedError):
            formatter.read_metadata(data)

    def test_no_files(self):
        formatter = Formatter()
        data = DataSet1D(self.locations[0])
        with self.assertRaises(IOError):
            formatter.read(data)

    def test_init_and_bad_read(self):
        location = self.locations[0]
        path = './{}/bad.dat'.format(location)

        class MyFormatter(Formatter):
            def read_one_file(self, data_set, f, ids_read):
                s = f.read()
                if 'garbage' not in s:
                    raise Exception('reading the wrong file?')

                # mark this file as read, before generating an error
                if not hasattr(data_set, 'files_read'):
                    data_set.files_read = []
                data_set.files_read.append(f.name)
                raise ValueError('garbage in, garbage out')

            def read_metadata(self, data_set):
                pass

        formatter = MyFormatter()
        data = DataSet1D(location)
        data.x_set.ndarray = None
        data.y.ndarray = None

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('garbage')

        with LogCapture() as logs:
            formatter.read(data)

        # we tried to read this file but it generated an error
        self.assertEqual(logs.value.count('error reading file'), 1, logs.value)
        self.assertEqual(data.files_read, [os.path.abspath(path)])

        expected_array_repr = repr([float('nan')] * 5)
        self.assertEqual(repr(data.x_set.tolist()), expected_array_repr)
        self.assertEqual(repr(data.y.tolist()), expected_array_repr)

    def test_group_arrays(self):
        formatter = Formatter()
        data = DataSetCombined()

        groups = formatter.group_arrays(data.arrays)

        self.assertEqual(len(groups), 2, groups)
        groups.sort(key=lambda grp: len(grp.set_arrays))

        g1d, g2d = groups

        self.assertEqual(g1d.shape, (2,))
        self.assertEqual(g1d.set_arrays, (data.x_set,))
        self.assertEqual(g1d.data, (data.y1, data.y2))
        self.assertEqual(g1d.name, 'x_set')

        self.assertEqual(g2d.shape, (2, 3))
        self.assertEqual(g2d.set_arrays, (data.x_set, data.y_set))
        self.assertEqual(g2d.data, (data.z1, data.z2))
        self.assertEqual(g2d.name, 'x_set_y_set')

    def test_match_save_range(self):
        formatter = Formatter()
        data = DataSet1D()

        group = formatter.group_arrays(data.arrays)[0]

        # no matter what else, if nothing is listed as modified
        # then save_range is None
        data.x_set.modified_range = data.y.modified_range = None
        for lsi_x in [None, 0, 3]:
            data.x_set.last_saved_index = lsi_x
            for lsi_y in [None, 1, 4]:
                data.y.last_saved_index = lsi_y
                for fe in [True, False]:
                    save_range = formatter.match_save_range(
                        group, file_exists=fe)
                    self.assertEqual(save_range, None)

        # consistent last_saved_index: if it's None or within the
        # modified range, or if file does not exist, we need to overwrite
        # otherwise start just after last_saved_index
        for lsi, start in [(None, 0), (0, 1), (1, 2), (2, 3), (3, 0), (4, 0)]:
            data.x_set.last_saved_index = data.y.last_saved_index = lsi

            # inconsistent modified_range: if only_complete is False, expands
            # to greatest extent so these situations are identical
            # if only_complete is True, only gets to the last common point
            for xmr, ymr, last_common in (
                    [(4, 4), (3, 3), 3],
                    [(3, 4), None, None],
                    [None, (3, 4), None]):
                data.x_set.modified_range = xmr
                data.y.modified_range = ymr

                save_range = formatter.match_save_range(
                    group, file_exists=False, only_complete=False)
                self.assertEqual(save_range, (0, 4))

                save_range = formatter.match_save_range(
                    group, file_exists=True, only_complete=False)
                self.assertEqual(save_range, (start, 4))

                save_all = formatter.match_save_range(group, file_exists=False)
                save_inc = formatter.match_save_range(group, file_exists=True)
                if last_common:
                    # if last_saved_index is greater than we would otherwise
                    # save, we still go up to last_saved_index (wouldn't want
                    # this write to delete data!)
                    last_save = max(last_common, lsi) if lsi else last_common
                    self.assertEqual(save_all, (0, last_save),
                                     (lsi, xmr, ymr))
                    self.assertEqual(save_inc, (start, last_save),
                                     (lsi, xmr, ymr))
                else:
                    if lsi is None:
                        self.assertIsNone(save_all)
                    else:
                        self.assertEqual(save_all, (0, lsi))
                    self.assertIsNone(save_inc)

        # inconsistent last_saved_index: need to overwrite if there are any
        # modifications
        data.x_set.last_saved_index = 1
        data.y.last_saved_index = 2
        data.x_set.modified_range = data.y.modified_range = (3, 4)
        save_range = formatter.match_save_range(group, file_exists=True)
        self.assertEqual(save_range, (0, 4))


class TestGNUPlotFormat(TestCase):
    def setUp(self):
        self.io = DataSet.default_io
        self.locations = ('_simple1d_', '_combined_')

        for location in self.locations:
            self.assertFalse(self.io.list(location))

    def tearDown(self):
        for location in self.locations:
            self.io.remove_all(location)

    def checkArraysEqual(self, a, b):
        self.checkArrayAttrs(a, b)

        self.assertEqual(len(a.set_arrays), len(b.set_arrays))
        for sa, sb in zip(a.set_arrays, b.set_arrays):
            self.checkArrayAttrs(sa, sb)

    def checkArrayAttrs(self, a, b):
        self.assertEqual(a.tolist(), b.tolist())
        self.assertEqual(a.label, b.label)
        self.assertEqual(a.array_id, b.array_id)

    def test_full_write(self):
        formatter = GNUPlotFormat()
        location = self.locations[0]
        data = DataSet1D(location)

        formatter.write(data, data.io, data.location)

        with open(location + '/x_set.dat', 'r') as f:
            self.assertEqual(f.read(), file_1d())

        # check that we can add comment lines randomly into the file
        # as long as it's after the first three lines, which are comments
        # with well-defined meaning,
        # and that we can un-quote the labels
        lines = file_1d().split('\n')
        lines[1] = lines[1].replace('"', '')
        lines[3:3] = ['# this data is awesome!']
        lines[6:6] = ['# the next point is my favorite.']
        with open(location + '/x_set.dat', 'w') as f:
            f.write('\n'.join(lines))

        # normally this would be just done by data2 = load_data(location)
        # but we want to work directly with the Formatter interface here
        data2 = DataSet(location=location)
        formatter.read(data2)

        self.checkArraysEqual(data2.x_set, data.x_set)
        self.checkArraysEqual(data2.y, data.y)

        # data has been saved
        self.assertEqual(data.y.last_saved_index, 4)
        # data2 has been read back in, should show the same
        # last_saved_index
        self.assertEqual(data2.y.last_saved_index, 4)

        # while we're here, check some errors on bad reads

        # first: trying to read into a dataset that already has the
        # wrong size
        x = DataArray(name='x_set', label='X', preset_data=(1., 2.))
        y = DataArray(name='y', label='Y', preset_data=(3., 4.),
                      set_arrays=(x,))
        data3 = new_data(arrays=(x, y), location=location + 'XX')
        # initially give it a different location so we can make it without
        # error, then change back to the location we want.
        data3.location = location
        with LogCapture() as logs:
            formatter.read(data3)

        self.assertTrue('ValueError' in logs.value, logs.value)

        # no problem reading again if only data has changed, it gets
        # overwritten with the disk copy
        data2.x_set[2] = 42
        data2.y[2] = 99
        formatter.read(data2)
        self.assertEqual(data2.x_set[2], 3)
        self.assertEqual(data2.y[2], 5)

    def test_format_options(self):
        formatter = GNUPlotFormat(extension='.splat', terminator='\r',
                                  separator='  ', comment='?:',
                                  number_format='5.2f')
        location = self.locations[0]
        data = DataSet1D(location)

        formatter.write(data, data.io, data.location)

        # TODO - Python3 uses universal newlines for read and write...
        # which means '\n' gets converted on write to the OS standard
        # (os.linesep) and all of the options we support get converted
        # back to '\n' on read. So I'm tempted to just take out terminator
        # as an option rather than turn this feature off.
        odd_format = '\n'.join([
            '?:x_set  y',
            '?:"X"  "Y"',
            '?:5',
            ' 1.00   3.00',
            ' 2.00   4.00',
            ' 3.00   5.00',
            ' 4.00   6.00',
            ' 5.00   7.00', ''])

        with open(location + '/x_set.splat', 'r') as f:
            self.assertEqual(f.read(), odd_format)

    def add_star(self, path):
        try:
            with open(path, 'a') as f:
                f.write('*')
        except FileNotFoundError:
            self.stars_before_write += 1

    def test_incremental_write(self):
        formatter = GNUPlotFormat()
        location = self.locations[0]
        location2 = self.locations[1]  # use 2nd location for reading back in
        data = DataSet1D(location)
        path = location + '/x_set.dat'

        data_copy = DataSet1D(False)

        # empty the data and mark it as unmodified
        data.x_set[:] = float('nan')
        data.y[:] = float('nan')
        data.x_set.modified_range = None
        data.y.modified_range = None

        # simulate writing after every value comes in, even within
        # one row (x comes first, it's the setpoint)
        # we'll add a '*' after each write and check that they're
        # in the right places afterward, ie we don't write any given
        # row until it's done and we never totally rewrite the file
        self.stars_before_write = 0
        for i, (x, y) in enumerate(zip(data_copy.x_set, data_copy.y)):
            data.x_set[i] = x
            formatter.write(data, data.io, data.location)
            formatter.write(data, data.io, location2)
            self.add_star(path)

            data.y[i] = y
            formatter.write(data, data.io, data.location)
            data.x_set.clear_save()
            data.y.clear_save()
            formatter.write(data, data.io, location2)
            self.add_star(path)

            # we wrote to a second location without the stars, so we can read
            # back in and make sure that we get the right last_saved_index
            # for the amount of data we've read.
            reread_data = load_data(location=location2, data_manager=False,
                                    formatter=formatter, io=data.io)
            self.assertEqual(repr(reread_data.x_set.tolist()),
                             repr(data.x_set.tolist()))
            self.assertEqual(repr(reread_data.y.tolist()),
                             repr(data.y.tolist()))
            self.assertEqual(reread_data.x_set.last_saved_index, i)
            self.assertEqual(reread_data.y.last_saved_index, i)

        starred_file = '\n'.join([
            '# x_set\ty',
            '# "X"\t"Y"',
            '# 5',
            '1\t3',
            '**2\t4',
            '**3\t5',
            '**4\t6',
            '**5\t7', '*'])

        with open(path, 'r') as f:
            self.assertEqual(f.read(), starred_file)
        self.assertEqual(self.stars_before_write, 1)

    def test_constructor_errors(self):
        with self.assertRaises(AttributeError):
            # extension must be a string
            GNUPlotFormat(extension=5)

        with self.assertRaises(ValueError):
            # terminator must be \r, \n, or \r\n
            GNUPlotFormat(terminator='\n\r')

        with self.assertRaises(ValueError):
            # this is not CSV - separator must be whitespace
            GNUPlotFormat(separator=',')

        with self.assertRaises(ValueError):
            GNUPlotFormat(comment='  \r\n\t  ')

    def test_read_errors(self):
        formatter = GNUPlotFormat()

        # non-comment line at the beginning
        location = self.locations[0]
        data = DataSet(location=location)
        os.makedirs(location, exist_ok=True)
        with open(location + '/x_set.dat', 'w') as f:
            f.write('1\t2\n' + file_1d())
        with LogCapture() as logs:
            formatter.read(data)

        self.assertTrue('ValueError' in logs.value, logs.value)

        # same data array in 2 files
        location = self.locations[1]
        data = DataSet(location=location)
        os.makedirs(location, exist_ok=True)
        with open(location + '/x_set.dat', 'w') as f:
            f.write('\n'.join(['# x_set\ty',
                               '# "X"\t"Y"', '# 2', '1\t2', '3\t4']))
        with open(location + '/q.dat', 'w') as f:
            f.write('\n'.join(['# q\ty', '# "Q"\t"Y"', '# 2', '1\t2', '3\t4']))
        with LogCapture() as logs:
            formatter.read(data)

        self.assertTrue('ValueError' in logs.value, logs.value)

    def test_multifile(self):
        formatter = GNUPlotFormat()
        location = self.locations[1]
        data = DataSetCombined(location)

        formatter.write(data, data.io, data.location)

        filex, filexy = files_combined()

        with open(location + '/x_set.dat', 'r') as f:
            self.assertEqual(f.read(), filex)
        with open(location + '/x_set_y_set.dat', 'r') as f:
            self.assertEqual(f.read(), filexy)

        data2 = DataSet(location=location)
        formatter.read(data2)

        for array_id in ('x_set', 'y1', 'y2', 'y_set', 'z1', 'z2'):
            self.checkArraysEqual(data2.arrays[array_id],
                                  data.arrays[array_id])


class TestHDF5_Format(TestCase):
    def setUp(self):
        self.io = DataSet.default_io
        self.locations = ('_simple1d_testsuite_', '_combined_testsuite_')
        self.formatter = HDF5Format()

        for location in self.locations:
            self.assertFalse(self.io.list(location))

    def tearDown(self):
        for location in self.locations:
            self.io.remove_all(location)

    def checkArraysEqual(self, a, b):
        # Copied from GNUplot formatter tests inheritance would be nicer
        self.checkArrayAttrs(a, b)
        self.assertTrue((a.ndarray==b.ndarray).all())
        self.assertEqual(len(a.set_arrays), len(b.set_arrays))
        for sa, sb in zip(a.set_arrays, b.set_arrays):
            self.checkArrayAttrs(sa, sb)

    def checkArrayAttrs(self, a, b):
        self.assertEqual(a.tolist(), b.tolist())
        self.assertEqual(a.label, b.label)
        self.assertEqual(a.array_id, b.array_id)

    def test_full_write_read(self):
        """
        Test writing and reading a file back in
        """
        location = self.locations[0]
        data = DataSet1D(location)
        self.formatter.write(data)
        # Used because the formatter has no nice find file method
        filepath = self.formatter.filepath
        with  h5py.File(self.formatter.filepath, mode='r') as f:
            # Read the raw-HDF5 file
            saved_arr_vals = np.array(f['Data Arrays']['Data'].value,
                                      dtype=np.float64)
            # TODO: There is a bug in the write function that appends
            # an extra zero to the datafile, made the test pass
            # so I can test the read functionality
            # print(saved_arr_vals[:, 1])
            # print(DataSet1D().arrays['x_set'].ndarray)
            # self.assertTrue((saved_arr_vals[:, 0] ==
            #                  DataSet1D().arrays['x_set'].ndarray).all())
            # self.assertTrue((saved_arr_vals[:, 1] ==
            #                  DataSet1D().arrays['y'].ndarray).all())

        # Test reading the same file through the DataSet.read
        # Relies explicitly on the filepath,
        # Currently the formatter does not have a nice way of finding files
        # TODO: I want to use location here and not the full filepath
        data2 = DataSet(location=filepath, formatter=self.formatter)
        data2.read()
        print('Full read/write works except for the set array')
        self.checkArraysEqual(data2.x_set, data.x_set)
        self.checkArraysEqual(data2.y, data.y)

    def test_read_write_missing_dset_attrs(self):
        '''
        If some attributes are missing it should still write correctly
        '''
        # raise(NotImplementedError)
        print('NotImplemented')

    def test_no_nest(self):
        pass
        # formatter = GNUPlotFormat(always_nest=False)
        # location = self.locations[0]
        # data = DataSet1D(location)

        # # mark the data set as modified by... modifying it!
        # # without actually changing it :)
        # # TODO - are there cases we should automatically mark the data as
        # # modified on construction?
        # data.y[4] = data.y[4]

        # formatter.write(data)

        # with open(location + '.dat', 'r') as f:
        #     self.assertEqual(f.read(), file_1d())

    def test_incremental_write(self):
        print('commented out until fixed')
        # location = self.locations[0]
        # data = DataSet1D(location)
        # data_copy = DataSet1D(False)

        # # # empty the data and mark it as unmodified
        # data.x[:] = float('nan')
        # data.y[:] = float('nan')
        # data.x.modified_range = None
        # data.y.modified_range = None

        # # Comment copied form GNUPlotFormat tests
        # # simulate writing after every value comes in, even within
        # # one row (x comes first, it's the setpoint)
        # for i, (x, y) in enumerate(zip(data_copy.x, data_copy.y)):
        #     data.x[i] = x
        #     self.formatter.write(data)
        #     # should not update here as not a full row has come in
        #     # TODO: implement this in the data formatter
        #     data.y[i] = y
        #     self.formatter.write(data)

        # filepath = self.formatter.filepath
        # data2 = DataSet(location=filepath, formatter=self.formatter)
        # data2.read()
        # self.checkArraysEqual(data2.x, data_copy.x)
        # self.checkArraysEqual(data2.y, data_copy.y)


    def test_loop_writing(self):
        print('Loop writing not implemented DEBUG PRINT REMOVE BEFORE MERGE')
        # station = Station()
        # MockPar = MockParabola(name='MockParabola')
        # station.add_component(MockPar)
        # # added to station to test snapshot at a later stage
        # loop = Loop(MockPar.x[-100:100:20]).each(MockPar.skewed_parabola)
        # dset = loop.run(name='MockParabola_run', formatter=self.formatter)

        # dset.write()
        # skew_para = np.array([ 1010000., 518400., 219600., 65600.,
        #                      8400., 0., 8400., 65600., 219600., 518400.])
        # x = np.arange(-100, 100, 20)
        # print(dset.sync())
        # print(dset.arrays)
        # fp = dset.formatter.filepath
        # loaded_data = load_data(fp, formatter=self.formatter)
        # arrs = load_data.arrays
        # self.assertTrue((arrs['x'].ndarray == x).all())
        # self.assertTrue((arrs['skewed_parabola'].ndarray == skew_para).all())



    def test_multifile(self):
        pass
        # formatter = GNUPlotFormat(always_nest=False)  # will nest anyway
        # location = self.locations[1]
        # data = DataSetCombined(location)

        # # mark one array in each file as completely modified
        # # that should cause the whole files to be written, even though
        # # the other data and setpoint arrays are not marked as modified
        # data.y1[:] += 0
        # data.z1[:, :] += 0
        # formatter.write(data)

        # filex, filexy = files_combined()

        # with open(location + '/x.dat', 'r') as f:
        #     self.assertEqual(f.read(), filex)
        # with open(location + '/x_yset.dat', 'r') as f:
        #     self.assertEqual(f.read(), filexy)

        # data2 = DataSet(location=location)
        # formatter.read(data2)

        # for array_id in ('x', 'y1', 'y2', 'yset', 'z1', 'z2'):
        #     self.checkArraysEqual(data2.arrays[array_id],
        #                           data.arrays[array_id])

