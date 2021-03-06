"""

gadgethdf
=========


Implementation of backend reader for GadgetHDF files by Andrew Pontzen.

The gadget array names are mapped into pynbody array mes according
to the mappings given by the config.ini section [gadgethdf-name-mapping].

The gadget particle groups are mapped into pynbody families according
to the mappings specified by the config.ini section [gadgethdf-type-mapping].
This can be many-to-one (many gadget particle types mapping into one
pynbody family), but only datasets which are common to all gadget types
will be available from pynbody.

Spanned files are supported. To load a range of files snap.0, snap.1, ... snap.n,
pass the filename 'snap'. If you pass snap.0, only that particular file will
be loaded.
"""


from __future__ import with_statement  # for py2.5

from .. import util, halo
from .. import family
from .. import units
from .. import config_parser
from . import SimSnap

import ConfigParser

import numpy as np
import functools, itertools
import warnings

import logging
logger = logging.getLogger('pynbody.snapshot.gadgethdf')

try:
    import h5py
except ImportError:
    h5py = None

_type_map = {}
for x in family.family_names():
    try:
        _type_map[family.get_family(x)] = \
                 [q for q in config_parser.get(
                     'gadgethdf-type-mapping', x).split(",")]
    except ConfigParser.NoOptionError:
        pass

_name_map, _rev_name_map = util.setup_name_maps('gadgethdf-name-mapping')
_translate_array_name = util.name_map_function(_name_map, _rev_name_map)


def _append_if_array(to_list, name, obj):
    if not hasattr(obj, 'keys'):
        to_list.append(name)


class DummyHDFData(object):

    """A stupid class to allow emulation of mass arrays for particles
    whose mass is in the header"""

    def __init__(self, value, length, dtype):
        self.value = value
        self.length = length
        self.shape = (length, )
        self.dtype = np.dtype(dtype)

    def __len__(self):
        return self.length

    def read_direct(self, target):
        target[:] = self.value


class HdfFileGenerator(object) :
    def __init__(self, filename, numfiles) :
        self.filename = filename
        self.numfiles = numfiles
        self._open_files = {}

    def __iter__(self) : 
        i = 0
        while i < self.numfiles : 
            try : 
                yield self._open_files[i]
            except KeyError: 
                yield h5py.File(self.filename+"."+str(i)+".hdf5", "r")
            i+=1

    def __getitem__(self, i) : 
        try : 
            return self._open_files[i]
        except KeyError : 
            self._open_files[i] = next(itertools.islice(self,i,i+1))
            return self._open_files[i]

    
class GadgetHDFSnap(SimSnap):
    """
    Class that reads HDF Gadget data
    """

    def __init__(self, filename):

        global config
        super(GadgetHDFSnap, self).__init__()

        self._filename = filename

        if not h5py.is_hdf5(filename):
            h1 = h5py.File(filename + ".0.hdf5", "r")
            numfiles = h1['Header'].attrs['NumFilesPerSnapshot']
            self._hdf = HdfFileGenerator(filename, numfiles)
        else:
            self._hdf = [h5py.File(filename, "r")]

        self._family_slice = {}

        self._loadable_keys = set([])
        self._family_arrays = {}
        self._arrays = {}
        self.properties = {}

        # determine which particle types are in the output

        my_type_map = {}

        for fam, g_types in _type_map.iteritems():
            my_types = []
            for x in g_types:
                # Get all keys from all hdf files
                for hdf in self._hdf:
                    if x in hdf.keys():
                        my_types.append(x)
                        break
            if len(my_types):
                my_type_map[fam] = my_types

        sl_start = 0
        for x in my_type_map:
            l = 0
            for name in my_type_map[x]:
                for hdf in self._hdf:
                    # Skip PartType if not in this hdf file
                    try:
                        l += hdf[name]['Coordinates'].shape[0]
                        k = self._get_hdf_allarray_keys(hdf[name])
                        self._loadable_keys = self._loadable_keys.union(set(k))
                    except KeyError as e:
                        if 'PartType' in name: continue
                        else: raise e
            self._family_slice[x] = slice(sl_start, sl_start + l)

            sl_start += l

        self._loadable_keys = [_translate_array_name(
            x, reverse=True) for x in self._loadable_keys]
        self._num_particles = sl_start

        self._my_type_map = my_type_map

        self._decorate()


    def _family_has_loadable_array(self, fam, name, subgroup = None):
        """Returns True if the array can be loaded for the specified family.
        If fam is None, returns True if the array can be loaded for all families."""

        if name == "mass":
            return True

        if subgroup is None : 
            hdf = self._hdf[0]
        else : 
            hdf = self._hdf[0][subgroup]


        if fam is None:
            return all([self._family_has_loadable_array(fam_x, name, subgroup) for fam_x in self._family_slice])

        else:
            translated_name = _translate_array_name(name)

            for n in _type_map[fam]:
                for hdf in self._hdf:
                    try:
                        if translated_name not in self._get_hdf_allarray_keys(hdf[n]):
                            return False
                    except KeyError as e:
                        if 'PartType' in n: continue
                        else: raise e
            return True

    def _get_all_particle_arrays(self, gtype, subgroup=None): 
        """Return all array names for a given gadget particle type"""

        # this is a hack to flatten a list of lists
        if subgroup is not None : 
            l = [item for sublist in [self._get_hdf_allarray_keys(x[subgroup][gtype]) for x in self._hdf] for item in sublist]
        else : 
            l = [item for sublist in [self._get_hdf_allarray_keys(x[gtype]) for x in self._hdf] for item in sublist]

        # now just return the unique items by converting to a set
        return list(set(l))

    def loadable_keys(self, fam=None):
        if fam is not None:
            return [x for x in self._loadable_keys if self._family_has_loadable_array(fam, x)]
        else:
            return [x for x in self._loadable_keys if self._family_has_loadable_array(None, x)]

        
    @staticmethod
    def _write(self, filename=None):
        raise RuntimeError("Not implemented")

        global config

        with self.lazy_off:  # prevent any lazy reading or evaluation

            if filename is None:
                filename = self._filename

            logger.info('Writing main file as %s', filename)

            self._hdf_out = h5py.File(filename, "w")

    def _write_array(self, array_name, filename=None):
        raise RuntimeError("Not implemented")

    @staticmethod
    def _get_hdf_allarray_keys(group):
        """Return all HDF array keys underneath group (includes nested groups)"""
        k = []
        group.visititems(functools.partial(_append_if_array, k))
        return k

    @staticmethod
    def _get_hdf_dataset(particle_group, hdf_name):
        """Return the HDF dataset resolving /'s into nested groups, and returning
        an apparent Mass array even if the mass is actually stored in the header"""

        if _translate_array_name(hdf_name,reverse=True)=='mass':
            try:
                pgid = int(particle_group.name[-1])
                mtab = particle_group.parent['Header'].attrs['MassTable'][pgid]
                if mtab > 0:
                    return DummyHDFData(mtab, particle_group['Coordinates'].shape[0], 
                                        particle_group['Coordinates'].dtype)
            except (IndexError, KeyError):
                pass

        ret = particle_group
        for tpart in hdf_name.split("/"):
            ret = ret[tpart]
        return ret

    @staticmethod
    def _get_cosmo_factors(hdf, arr_name) :
        """Return the cosmological factors for a given array"""
        match = [s for s in GadgetHDFSnap._get_hdf_allarray_keys(hdf) if ((arr_name in s) & ('PartType' in s))]
        if len(match) > 0 : 
            aexp = hdf[match[0]].attrs['aexp-scale-exponent']
            hexp = hdf[match[0]].attrs['h-scale-exponent']
            return units.a**util.fractions.Fraction.from_float(float(aexp)).limit_denominator(), units.h**util.fractions.Fraction.from_float(float(hexp)).limit_denominator()
        else : 
            return units.Unit('1.0'), units.Unit('1.0')


    @staticmethod
    def _get_hdf_units(hdfattrs, unitvar, cgsvar) :
        """Return the units based on HDF attributes VarDescription"""

        # Default is '1.0' unit
        arr_units = units.Unit('1.0')
        conversion = 1.0

        VarDescription = str(hdfattrs['VarDescription'])
        CGSConversionFactor = float(hdfattrs['CGSConversionFactor'])
        aexp = hdfattrs['aexp-scale-exponent']
        hexp = hdfattrs['h-scale-exponent']

        # First test using the gadget Unit list, U_M, U_L, U_V
        for unitname in unitvar.keys() :
            power = 1.
            if unitname in VarDescription : 
                sstart = VarDescription.find(unitname)
                if sstart > 0 :
                    if VarDescription[sstart-1] == "/" :
                        power *= -1.
                if len(VarDescription) > sstart+len(unitname):
                    # Just check we're not at the end of the line
                    if VarDescription[sstart+len(unitname)] == '^' :
                        ## Has an index, check if this is negative
                        if VarDescription[sstart+len(unitname)+1] == "-" :
                            power *= -1.
                            power *= float(VarDescription[sstart+len(unitname)+2:-1].split()[0]) ## Search for the power
                        else:
                            power *= float(VarDescription[sstart+len(unitname)+1:-1].split()[0]) ## Search for the power
                if not np.allclose(power, 0.0):
                    arr_units *= unitvar[unitname]**util.fractions.Fraction.from_float(float(power)).limit_denominator()
                if not np.allclose(power, 0.0):
                    conversion *= unitvar[unitname].in_units(cgsvar[unitname])**util.fractions.Fraction.from_float(float(power)).limit_denominator()

        # sanity check
        if not np.allclose(conversion,CGSConversionFactor,rtol=1e-3):
            print("Error with unit read out from HDF ")
            print("conversion is ",conversion)
            print("but HDF requires ",CGSConversionFactor)

        ## Now the cosmological units
        if not np.allclose(aexp, 0.0):
            arr_units *= (units.a)**util.fractions.Fraction.from_float(float(aexp)).limit_denominator()
        if not np.allclose(hexp, 0.0):    
            arr_units *= (units.h)**util.fractions.Fraction.from_float(float(hexp)).limit_denominator()
  
        return arr_units


    def _load_array(self, array_name, fam=None, subgroup = None):
        if not self._family_has_loadable_array(fam, array_name, subgroup):
            raise IOError("No such array on disk")
        else:
            if fam is not None:
                famx = fam
            else:
                famx = self._family_slice.keys()[0]

            translated_name = _translate_array_name(array_name)

            # Set the global units for these arrays

            # Read in the attribute units from SubFind
            try : 
                atr = self._hdf[0]['Units'].attrs
            except KeyError : 
                warnings.warn("No unit information found!",RuntimeWarning)
                return                        

            # Define the SubFind units, we will parse the attribute VarDescriptions for these
            vel_unit = atr['UnitVelocity_in_cm_per_s']*units.cm/units.s
            dist_unit = atr['UnitLength_in_cm']*units.cm
            mass_unit = atr['UnitMass_in_g']*units.g
            time_unit = atr['UnitTime_in_s']*units.s

            # Create a dictionary for the units, this will come in handy later
            unitvar = {'U_V' : vel_unit, 'U_L' : dist_unit, 'U_M' : mass_unit, 
                       'U_T' : time_unit, '[K]' : units.K, 
                       'SEC_PER_YEAR' : units.yr, 'SOLAR_MASS' : units.Msol}
            # Last two units are to catch occasional arrays like StarFormationRate which don't 
            # follow the patter of U_ units unfortunately
             
            cgsvar = {'U_M' : 'g', 'SOLAR_MASS' : 'g', 'U_T': 's', 
                      'SEC_PER_YEAR': 's', 'U_V' : 'cm s**-1', 'U_L' : 'cm', '[K]' : 'K'}

            # this next chunk of code is just to determine the
            # dimensionality of the data

            # not all arrays are present in all hdfs so need to loop
            # until we find one
            for hdf0 in self._hdf : 
                if subgroup is not None : 
                    hdf0 = hdf0[subgroup]     
                try : 
                    dset0 = self._get_hdf_dataset(hdf0[
                        self._my_type_map[famx][0]], translated_name)
                    if hasattr(dset0, "attrs"):
                        units0 = self._get_hdf_units(dset0.attrs, unitvar, cgsvar)
                    else:
                        units0 = units.NoUnit()
                    break

                except KeyError: 
                    continue

            assert len(dset0.shape) <= 2
            dy = 1
            if len(dset0.shape) > 1:
                dy = dset0.shape[1]

            # check if the dimensions make sense -- if
            # not, assume we're looking at an array that
            # is 3D and cross your fingers
            npart = len(hdf0[self._my_type_map[famx][0]]['ParticleIDs'])
            if len(dset0) != npart :
                dy = len(dset0)/npart                     

            dtype = dset0.dtype

            # got the dimension -- now make the arrays and load the data

            if fam is None:
                self._create_array(array_name, dy, dtype=dtype)
                self[array_name].set_default_units()
                self[array_name].units = units0 
            else:
                self[fam]._create_array(array_name, dy, dtype=dtype)
                self[fam][array_name].set_default_units()
                self[fam][array_name].units = units0 

            if fam is not None:
                fams = [fam]
            else:
                fams = self._family_slice.keys()

            for f in fams:
                i0 = 0
                for t in self._my_type_map[f]:
                    for hdf in self._hdf:
                        if subgroup is not None : 
                            hdf = hdf[subgroup]
                        try : 
                            npart = len(hdf[t]['ParticleIDs'])
                        except KeyError: 
                            npart = 0
                        
                        if npart > 0 : 
                            dataset = self._get_hdf_dataset(hdf[t], translated_name)

                            # check if the dimensions make sense -- if
                            # not, assume we're looking at an array that
                            # is 3D and cross your fingers
                            if len(dataset) != npart : 
                                temp = dataset[:].reshape((len(dataset)/3,3))
                                i1 = i0+len(temp)
                                self[f][array_name][i0:i1] = temp
                            
                            else : 
                                i1 = i0+len(dataset)
                                dataset.read_direct(self[f][array_name][i0:i1])

                            i0 = i1

### This was put in in a recent pull request -- it's not immediately obvious to me 
### that the dimensinality checks would work out so I'm keeping the checks above and
### commenting out this block below -- it would replace lines 348 - 371
 #                       try:
 #                           dataset = self._get_hdf_dataset(
 #                               hdf[t], translated_name)
 #                       except KeyError as e:
 #                           if 'parttype' in e.args[0]: continue
 #                           else: raise e
 #                       i1 = i0 + len(dataset)
 #                       dataset.read_direct(self[f][array_name][i0:i1])
 #                       i0 = i1

    @staticmethod
    def _can_load(f):
        try:
            if h5py.is_hdf5(f) or h5py.is_hdf5(f+".0.hdf5") and ('sub' not in f):
                return True
            else:
                return False
        except AttributeError:
            if "hdf5" in f:
                warnings.warn(
                    "It looks like you're trying to load HDF5 files, but python's HDF support (h5py module) is missing.", RuntimeWarning)
            return False

@GadgetHDFSnap.decorator
def do_properties(sim):
    atr = sim._hdf[0]['Header'].attrs

    # expansion factor could be saved as redshift
    try:
        sim.properties['a'] = atr['ExpansionFactor']
    except KeyError:
        sim.properties['a'] = 1. / (1 + atr['Redshift'])

    # time unit might not be set in the attributes
    try:
        sim.properties['time'] = units.Gyr * atr['Time_GYR']
    except KeyError:
        pass

    # not all omegas need to be specified in the attributes
    try:
        sim.properties['omegaB0'] = atr['OmegaBaryon']
    except KeyError:
        pass

    sim.properties['omegaM0'] = atr['Omega0']
    sim.properties['omegaL0'] = atr['OmegaLambda']
    sim.properties['boxsize'] = atr['BoxSize']
    sim.properties['z'] = (1. / sim.properties['a']) - 1
    sim.properties['h'] = atr['HubbleParam']
    for s in sim._hdf[0]['Header'].attrs:
        if s not in ['ExpansionFactor', 'Time_GYR', 'Omega0', 'OmegaBaryon', 'OmegaLambda', 'BoxSize', 'HubbleParam']:
            sim.properties[s] = sim._hdf[0]['Header'].attrs[s]

@GadgetHDFSnap.decorator
def do_units(sim):

    # this doesn't seem to be standard -- maybe use the convention
    # from tipsy.py and set cosmo = True if there is a hubble constant
    # specified?
    try:
        cosmo = (sim._hdf[0]['Parameters'][
            'NumericalParameters'].attrs['ComovingIntegrationOn']) != 0
    except KeyError:
        cosmo = 'HubbleParam' in sim._hdf[0]['Header'].attrs.keys()

    try:
        atr = sim._hdf[0]['Units'].attrs
    except KeyError:
        # Use default values, from default_config.ini if necessary
        vel_unit = config_parser.get('gadget-units', 'vel')
        dist_unit = config_parser.get('gadget-units', 'pos')
        mass_unit = config_parser.get('gadget-units', 'mass')
        warnings.warn(
            "No unit information found: using gadget-units.", RuntimeWarning)
        sim._file_units_system = [units.Unit(x) for x in [
                vel_unit, dist_unit, mass_unit, "K"]]
        return

    vel_unit = atr['UnitVelocity_in_cm_per_s']*units.cm/units.s
    dist_unit = atr['UnitLength_in_cm']*units.cm
    mass_unit = atr['UnitMass_in_g']*units.g

    if cosmo:
        for fac in GadgetHDFSnap._get_cosmo_factors(sim._hdf[0],'Coordinates') : dist_unit *= fac
        for fac in GadgetHDFSnap._get_cosmo_factors(sim._hdf[0],'Velocity') : vel_unit *= fac
        for fac in GadgetHDFSnap._get_cosmo_factors(sim._hdf[0],'Mass') : mass_unit *= fac

    sim._file_units_system = [units.Unit(x) for x in [
                              vel_unit, dist_unit, mass_unit, "K"]]


###################
# SubFindHDF class
###################

class SubFindHDFSnap(GadgetHDFSnap) : 
    """
    Class to read Gadget's SubFind HDF data
    """

    def __init__(self, filename) : 
        
        global config
        super(SubFindHDFSnap,self).__init__(filename)

        # the super constructor does almost nothing because most of
        # the relevant data on particles is stored in the FOF group
        # attributes -- but it does set up the array and slice
        # dictionaries and the properties dictionary

        # get the properties from the FOF HDF group and other metadata
        self._decorate()

        numfiles = self.properties['NTask']
                    
        self._hdf = HdfFileGenerator(filename, numfiles)

        # set up the particle type mapping
        my_type_map = {}

        # defines the link between PartType0, 1, 4 in my_types 
        # and the pynbody nomenclature, gas, dm and stars in fam
        for fam, g_types in _type_map.iteritems() : 
            my_types = []
            for x in g_types :
                if x in self._hdf[0]['FOF'].keys() : 
                    my_types.append(x)
            if len(my_types) : 
                my_type_map[fam] = my_types

        # set up family slices
        sl_start = 0
        self._loadable_keys = set([])
        for x in my_type_map:
            l = 0
            for name in my_type_map[x]:
                for hdf in self._hdf:
                    l += hdf['FOF'].attrs['Number_per_Type'][int(name[-1])]
            self._family_slice[x] = slice(sl_start, sl_start+l)

            k = self._get_hdf_allarray_keys(self._hdf[0]['FOF'][name])
            self._loadable_keys = self._loadable_keys.union(set(k))
            sl_start += l
        self._loadable_keys = [_translate_array_name(
            x, reverse=True) for x in self._loadable_keys]
        
        self._num_particles = sl_start

        self._my_type_map = my_type_map


    def _load_array(self, array_name, fam=None, subgroup = 'FOF') : 
        return GadgetHDFSnap._load_array(self, array_name, fam, subgroup)

    def halos(self) : 
        return halo.SubFindHDFHaloCatalogue(self)

    @staticmethod
    def _can_load(f):
        try:
            if h5py.is_hdf5(f) or h5py.is_hdf5(f+".0.hdf5"):
                return True
            else:
                return False
        except AttributeError:
            if "hdf5" in f:
                warnings.warn(
                    "It looks like you're trying to load HDF5 files, but python's HDF support (h5py module) is missing.", RuntimeWarning)
            return False

            
@SubFindHDFSnap.decorator
def do_properties(sim): 

    atr = sim._hdf[0]['FOF'].attrs

    for s in atr : 
        sim.properties[s] = atr[s]

## Gadget has internal energy variable
@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def u(self) :
    """Gas internal energy derived from snapshot variable or temperature"""
    try:    
        u = self['InternalEnergy']        
    except KeyError:
        gamma = 5./3
        u = self['temp']*units.k/(self['mu']*units.m_p*(gamma-1))

    return u

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def p(sim) :
    """Calculate the pressure for gas particles, including polytropic equation of state gas"""

    critpres = 2300. * units.K * units.m_p / units.cm**3 ## m_p K cm^-3
    critdens = 0.1 * units.m_p / units.cm**3 ## m_p cm^-3
    gammaeff = 4./3.

    oneos = sim.g['OnEquationOfState'] == 1.

    p = sim.g['rho'].in_units('m_p cm**-3') * sim.g['temp'].in_units('K')
    p[oneos] = critpres * (sim.g['rho'][oneos].in_units('m_p cm**-3')/critdens)**gammaeff

    return p

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HII(sim) :
    """Number of HII ions per proton mass"""

    return sim.g["hydrogen"] - sim.g["HI"]

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HeIII(sim) :
    """Number of HeIII ions per proton mass"""

    return sim.g["hetot"] - sim.g["HeII"] - sim.g["HeI"]

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def ne(sim) :
    """Number of electrons per proton mass, ignoring the contribution from He!"""
    ne = sim.g["HII"]  #+ sim["HeII"] + 2*sim["HeIII"]
    ne.units = units.m_p**-1

    return ne

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def rho_ne(sim) :
    """Electron number density per SPH particle, currently ignoring the contribution from He!"""

    return sim.g["ne"].in_units("m_p**-1") * sim.g["rho"].in_units("m_p cm**-3")

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def dm(sim) :
    """Dispersion measure per SPH particle currently ignoring n_e contribution from He """

    return sim.g["rho_ne"] 

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def cosmodm(sim) :
    """Cosmological Dispersion measure per SPH particle includes (1+z) factor, currently ignoring n_e contribution from He """

    return sim.g["rho_ne"] * (1. + sim.g["redshift"])
@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def redshift(sim) :
    """Redshift from LoS Velocity 'losvel' """

    return np.exp( sim['losvel'].in_units('c') ) - 1.

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def doppler_redshift(sim) :
    """Doppler Redshift from LoS Velocity 'losvel' using SR """

    return np.sqrt( (1. + sim['losvel'].in_units('c')) / (1. - sim['losvel'].in_units('c'))  ) - 1. 

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def em(sim) :
    """Emission Measure (n_e^2) per particle to be integrated along LoS"""

    return sim.g["rho_ne"]*sim.g["rho_ne"]

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def halpha(sim) :
    """H alpha intensity (based on Emission Measure n_e^2) per particle to be integrated along LoS"""

    ## Rate at which recombining electrons and protons produce Halpha photons. 
    ## Case B recombination assumed from Draine (2011)    
    #alpha = 2.54e-13 * (sim.g['temp'].in_units('K') / 1e4)**(-0.8163-0.0208*np.log(sim.g['temp'].in_units('K') / 1e4))
    #alpha.units = units.cm**(3) * units.s**(-1)

    ## H alpha intensity = coeff * EM 
    ## where coeff is h (c / Lambda_Halpha) / 4Pi) and EM is int rho_e * rho_p * alpha
    ## alpha = 7.864e-14 T_1e4K from http://astro.berkeley.edu/~ay216/08/NOTES/Lecture08-08.pdf
    coeff = (6.6260755e-27) * (299792458. / 656.281e-9) / (4.*np.pi) ## units are erg sr^-1
    alpha = coeff * 7.864e-14 * (1e4 / sim.g['temp'].in_units('K')) 

    alpha.units = units.erg * units.cm**(3) * units.s**(-1) * units.sr**(-1) ## It's intensity in erg cm^3 s^-1 sr^-1

    return alpha * sim["em"] # Flux erg cm^-3 s^-1 sr^-1

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def c_n_sq(sim) :
    """Turbulent amplitude C_N^2 for use in SM calculations (e.g. Eqn 20 of Macquart & Koay 2013 ApJ 776 2) """

    ## Spectrum of turbulence below the SPH resolution, assume Kolmogorov
    beta = 11./3.
    L_min = 0.1*units.Mpc
    c_n_sq = ((beta - 3.)/((2.)*(2.*np.pi)**(4.-beta)))*L_min**(3.-beta)*sim["em"]
    c_n_sq.units = units.m**(-20,3)

    return c_n_sq

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def hetot(self) :
    return self["He"]

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def hydrogen(self) :
    return self["H"]

## Need to use the ionisation fraction calculation here which gives ionisation fraction
## based on the gas temperature, density and redshift for a CLOUDY table
@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HI(sim) :
    """Fraction of Neutral Hydrogen HI use limited CLOUDY table"""

    import pynbody.analysis.hifrac

    return pynbody.analysis.hifrac.calculate(sim.g,ion='hi')

## Need to use the ionisation fraction calculation here which gives ionisation fraction
## based on the gas temperature, density and redshift for a CLOUDY table, then applying
## selfshielding for the dense, star forming gas on the equation of state
@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HIeos(sim) :
    """Fraction of Neutral Hydrogen HI use limited CLOUDY table, assuming dense EoS gas is selfshielded"""

    import pynbody.analysis.hifrac

    return pynbody.analysis.hifrac.calculate(sim.g,ion='hi', selfshield='eos')

## Need to use the ionisation fraction calculation here which gives ionisation fraction
## based on the gas temperature, density and redshift for a CLOUDY table, then applying
## selfshielding for the dense, star forming gas on the equation of state AND a further
## pressure based limit for 
@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HID12(sim) :
    """Fraction of Neutral Hydrogen HI use limited CLOUDY table, using the Duffy +12a prescription for selfshielding"""

    import pynbody.analysis.hifrac

    return pynbody.analysis.hifrac.calculate(sim.g,ion='hi', selfshield='duffy12')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HeI(sim) :
    """Fraction of Helium HeI"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='hei')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def HeII(sim) :
    """Fraction of Helium HeII"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='heii')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def OI(sim) :
    """Fraction of Oxygen OI"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='oi')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def OII(sim) :
    """Fraction of Oxygen OII"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='oii')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def OVI(sim) :
    """Fraction of Oxygen OVI"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='ovi')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def CIV(sim) :
    """Fraction of Carbon CIV"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='civ')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def NV(sim) :
    """Fraction of Nitrogen NV"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='nv')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def SIV(sim) :
    """Fraction of Silicon SiIV"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='siiv')

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def MGII(sim) :
    """Fraction of Magnesium MgII"""

    import pynbody.analysis.ionfrac

    return pynbody.analysis.ionfrac.calculate(sim.g,ion='mgii')

# The Solar Abundances used in Gadget-3 OWLS / Eagle / Smaug sims
XSOLH=0.70649785
XSOLHe=0.28055534
XSOLC=2.0665436E-3
XSOLN=8.3562563E-4
XSOLO=5.4926244E-3
XSOLNe=1.4144605E-3
XSOLMg=5.907064E-4
XSOLSi=6.825874E-4
XSOLS=4.0898522E-4
XSOLCa=6.4355E-5
XSOLFe=1.1032152E-3

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def feh(self) :
    minfe = np.amin(self['Fe'][np.where(self['Fe'] > 0)])
    self['Fe'][np.where(self['Fe'] == 0)]=minfe
    return np.log10(self['Fe']/self['H']) - np.log10(XSOLFe/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def sixh(self) :
    minsi = np.amin(self['Si'][np.where(self['Si'] > 0)])
    self['Si'][np.where(self['Si'] == 0)]=minsi
    return np.log10(self['Si']/self['Si']) - np.log10(XSOLSi/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def sxh(self) :
    minsx = np.amin(self['S'][np.where(self['S'] > 0)])
    self['S'][np.where(self['S'] == 0)]=minsx
    return np.log10(self['S']/self['S']) - np.log10(XSOLS/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def mgxh(self) :
    minmg = np.amin(self['Mg'][np.where(self['Mg'] > 0)])
    self['Mg'][np.where(self['Mg'] == 0)]=minmg
    return np.log10(self['Mg']/self['Mg']) - np.log10(XSOLMg/XSOLH)

@GadgetHDFSnap.derived_quantity    
@SubFindHDFSnap.derived_quantity
def oxh(self) :
    minox = np.amin(self['O'][np.where(self['O'] > 0)])
    self['O'][np.where(self['O'] == 0)]=minox
    return np.log10(self['O']/self['H']) - np.log10(XSOLO/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def nexh(self) :
    minne = np.amin(self['Ne'][np.where(self['Ne'] > 0)])
    self['Ne'][np.where(self['Ne'] == 0)]=minne
    return np.log10(self['Ne']/self['Ne']) - np.log10(XSOLNe/XSOLH)

@SubFindHDFSnap.derived_quantity
def hexh(self) :
    minhe = np.amin(self['He'][np.where(self['He'] > 0)])
    self['He'][np.where(self['He'] == 0)]=minhe
    return np.log10(self['He']/self['He']) - np.log10(XSOLHe/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def cxh(self) :
    mincx = np.amin(self['C'][np.where(self['C'] > 0)])
    self['C'][np.where(self['C'] == 0)]=mincx
    return np.log10(self['C']/self['H']) - np.log10(XSOLC/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def caxh(self) :
    mincax = np.amin(self['Ca'][np.where(self['Ca'] > 0)])
    self['Ca'][np.where(self['Ca'] == 0)]=mincax
    return np.log10(self['Ca']/self['H']) - np.log10(XSOLCa/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def nxh(self) :
    minnx = np.amin(self['N'][np.where(self['N'] > 0)])
    self['N'][np.where(self['N'] == 0)]=minnx
    return np.log10(self['N']/self['H']) - np.log10(XSOLH/XSOLH)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def ofe(self) :
    minox = np.amin(self['O'][np.where(self['O'] > 0)])
    self['O'][np.where(self['O'] == 0)]=minox
    minfe = np.amin(self['Fe'][np.where(self['Fe'] > 0)])
    self['Fe'][np.where(self['Fe'] == 0)]=minfe
    return np.log10(self['O']/self['Fe']) - np.log10(XSOLO/XSOLFe)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def mgfe(sim) :
    minmg = np.amin(sim['Mg'][np.where(sim['Mg'] > 0)])
    sim['Mg'][np.where(sim['Mg'] == 0)]=minmg
    minfe = np.amin(sim['Fe'][np.where(sim['Fe'] > 0)])
    sim['Fe'][np.where(sim['Fe'] == 0)]=minfe
    return np.log10(sim['Mg']/sim['Fe']) - np.log10(XSOLMg/XSOLFe)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def nefe(sim) :
    minne = np.amin(sim['Ne'][np.where(sim['Ne'] > 0)])
    sim['Ne'][np.where(sim['Ne'] == 0)]=minne
    minfe = np.amin(sim['Fe'][np.where(sim['Fe'] > 0)])
    sim['Fe'][np.where(sim['Fe'] == 0)]=minfe
    return np.log10(sim['Ne']/sim['Fe']) - np.log10(XSOLNe/XSOLFe)

@GadgetHDFSnap.derived_quantity
@SubFindHDFSnap.derived_quantity
def sife(sim) :
    minsi = np.amin(sim['Si'][np.where(sim['Si'] > 0)])
    sim['Si'][np.where(sim['Si'] == 0)]=minsi
    minfe = np.amin(sim['Fe'][np.where(sim['Fe'] > 0)])
    sim['Fe'][np.where(sim['Fe'] == 0)]=minfe
    return np.log10(sim['Si']/sim['Fe']) - np.log10(XSOLSi/XSOLFe)
