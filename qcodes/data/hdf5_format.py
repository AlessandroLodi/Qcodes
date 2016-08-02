import numpy as np
import h5py
import re
import math
import os

from .data_array import DataArray
from .format import Formatter

"""
list for things that are still missing
* dataset has no way of including units
* there is an assumption that data arrays are preallocated in the dataset.
  this breaks down when measurements are interupted before they terminate
  and/or when doing adaptive measurements where you do not know beforehand
  how many datapoints you will get.
* Add more robust way of incremental write (check if all arrays are ready
  to increment)

"""


class HDF5Format(Formatter):
    """
    HDF5 formatter for saving qcodes datasets
    """
    def __init__(self):
        """
        Instantiates the datafile using the location provided by the io_manager
        see h5py detailed info
        """
        self.data_object = None

    def _create_file(self, filepath):
        folder, _filename = os.path.split(filepath)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        self.data_object = h5py.File(filepath, 'a')

    def read(self, data_set):
        """
        Tested that it correctly opens a file, needs a better way to
        find the actual file. This is not part of the formatter at this point
        """
        io_manager = data_set.io
        location = data_set.location
        filepath = location
        self.data_object = h5py.File(filepath, 'r+')
        arrays = data_set.arrays
        for i, col_name in enumerate(
                self.data_object['Data Arrays']['Data'].attrs['column names']):
            # Decoding string is needed because of h5py/issues/379
            col_name = col_name.decode()
            name = col_name # will be overwritten if not in file
            dat_arr = self.data_object['Data Arrays']['Data']
            if hasattr(dat_arr, 'label'):
                label = dat_arr.attrs['labels'][i].decode()
            else:
                label=None
            if hasattr(dat_arr, 'names'):
                name = dat_arr.attrs['names'][i].decode()
            if hasattr(dat_arr, 'unit'):
                unit = dat_arr.attrs['units'][i].decode()
            else:
                unit = None
            d_array = DataArray(
                name=name, array_id=col_name, label=label, parameter=None,
                preset_data=self.data_object['Data Arrays']['Data'].value[:, i])
            data_set.add_array(d_array)

    def write(self, data_set, force_write=False):
        """
        """
        if self.data_object == None or force_write:
            # Create the file if it is not there yet
            io_manager = data_set.io
            # FIXME: use the default location provider and not the custom one here
            location = data_set.location
            self.filepath = io_manager.join(
                io_manager.base_location,
                data_set.location_provider(io_manager)+'/'+location+'.hdf5')
            self._create_file(self.filepath)

        if 'Data Arrays' not in self.data_object.keys():
            self.arr_group = self.data_object.create_group('Data Arrays')
        for array_id in data_set.arrays.keys():
            if array_id not in self.arr_group.keys() or force_write:
                self._create_dataarray_dset(array=data_set[array_id],
                                            group=self.arr_group)
            dset = self.arr_group[array_id]
            # Resize the dataset and add the new values

            # dataset refers to the hdf5 dataset here
            datasetshape = dset.shape
            old_datasetlen = datasetshape[0]
            x = data_set.arrays[array_id]
            new_data_length = len(x[~np.isnan(x)])
            new_datasetshape = (new_data_length,
                                datasetshape[1])
            dset.resize(new_datasetshape)
            dset[old_datasetlen:new_data_length] = \
                data_set[array_id][old_datasetlen:new_data_length]

    def _create_dataarray_dset(self, array, group):
        '''
        input arguments
        array:  Dataset data array
        group:  group in the hdf5 file where the dset will be created

        creates a hdf5 datasaset that represents the data array.
        '''
        dset = group.create_dataset(
            array.array_id, (0, len(array.units)),
            maxshape=(None, len(array.units)))
        if hasattr(array, 'label'):
            label = array.label
        else:
            label = array.array_id
        if hasattr(array, 'name'):
            name = array.name
        else:
            name = array.array_id
        if hasattr(array, 'units'):
            units = array.units
        else:
            units += ['']
        dset.attrs['label'] = _encode_to_utf8(str(label))
        dset.attrs['name'] = _encode_to_utf8(str(name))
        dset.attrs['units'] = _encode_to_utf8(str(units))
        return dset


    def _create_data_arrays_grp(self, arrays):
        self.data_arrays_grp = self.data_object.create_group('Data Arrays')
        # Allows reshaping but does not allow adding extra parameters
        self.dset = self.data_arrays_grp.create_dataset(
            'Data', (0, len(arrays.keys())),
            maxshape=(None, len(arrays.keys())))
        self.dset.attrs['column names'] = _encode_to_utf8(arrays.keys())
        labels= []
        names = []
        units = []
        for key in arrays.keys():
            arr = arrays[key]
            if hasattr(arr, 'label'):
                labels += [arr.label]
            else:
                labels += [key]
            if hasattr(arr, 'name'):
                names += [arr.name]
            else:
                labels += [key]
            if hasattr(arr, 'units'):
                units += [arr.units]
            else:
                units += ['']
        # _encode_to_utf8(str(...)) ensures None gets encoded for h5py aswell
        self.dset.attrs['labels'] = _encode_to_utf8(str(labels))
        self.dset.attrs['names'] = _encode_to_utf8(str(names))
        self.dset.attrs['units'] = _encode_to_utf8(str(units))
        # Added to tell analysis how to extract the data
        self.data_arrays_grp.attrs['datasaving_format'] = _encode_to_utf8(
            'QCodes hdf5 v0.1')

    def write_metadata(self, data_set, io_manager, location, read_first=True):
        print('Write meta data is passing')

    def save_instrument_snapshot(self, snapshot, *args):
        """
        uses QCodes station snapshot to save the last known value of any
        parameter. Only saves the value and not the update time (which is
        known in the snapshot)

        META DATA GROUP
        """
        # Todo: merge with write metadata
        # TODO:  should be pretty easy to add this but am waiting
        # for the metadata of @Merlinsmiles
        set_grp = data_object.create_group('Meta-data')
        inslist = dict_to_ordered_tuples(self.station.instruments)
        for (iname, ins) in inslist:
            instrument_grp = set_grp.create_group(iname)
            par_snap = ins.snapshot()['parameters']
            parameter_list = dict_to_ordered_tuples(par_snap)
            for (p_name, p) in parameter_list:
                try:
                    val = str(p['value'])
                except KeyError:
                    val = ''
                instrument_grp.attrs[p_name] = str(val)


def _encode_to_utf8(s):
    """
    Required because h5py does not support python3 strings
    """
    # converts byte type to string because of h5py datasaving
    if type(s) == str:
        s = s.encode('utf-8')
    # If it is an array of value decodes individual entries
    elif type(s) == np.ndarray or list:
        s = [s.encode('utf-8') for s in s]
    return s
