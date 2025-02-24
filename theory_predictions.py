#####################################################################################################
# - Estimates the n(z) distributions and then computes theoretical predictions 
#   for the power spectra.
#####################################################################################################

import pyccl as ccl
import h5py
import numpy as np
from functools import reduce
from output_utils import colour_string
import cell_utils as cu
import sys
from configuration import PipelineConfig as PC

### SETTINGS ###
config_file = sys.argv[1]
cf = PC(config_file, stage='theoryPredictions')


###################
#### FUNCTIONS ####
###################

def compute_nofz(fd):
	'''
	Computes estimates of the n(z) distributions in each redshift bin for a given
	field and returns them in a dictionary.

	Parameters
	----------
	fd: str
		Name of the field being analysed.
	
	Returns
	-------
	nofz: dict
		Dictionary containing the redshift bin centres and the n(z) values in each
		of those bins for each tomographic bin.
	'''


	#lists for the best z estimates and the random MC draws
	z_best, z_mc = [], []
	#dictionary for containing the masks for selecting each sample
	masks = {k : [] for k in cf.samples}

	#if provided field is 'combined', need to load data from all fields
	if fd == 'combined':
		fd = ['hectomap', 'spring', 'autumn']
	else:
		fd = [fd]
	
	for f in fd:
		with h5py.File(cf.paths.out + f + '/' + cf.cats.main, 'r') as hf:
			gr = hf['photometry']
			z_best.append(gr[f'{cf.key_cols.zphot}'][:])
			z_mc.append(gr[f'{cf.key_cols.zphot_mc}'][:])
			sample_masks = cf.get_samples(gr)
			for k in cf.samples:
				masks[k].append(sample_masks[k])
	
	#concatenate the lists of arrays
	z_best = np.concatenate(z_best)
	z_mc = np.concatenate(z_mc)
	for k in masks:
		masks[k] = np.concatenate(masks[k])

	#determine the bin edges and centres to use for the n(z) histograms
	bins = np.arange(0., z_mc.max()+cf.dz, cf.dz)
	bin_centres = (bins[1:] + bins[:-1]) / 2
	
	#set up a dictionary for containing the n(z)s for the different bins
	nofz = {'z' : bin_centres}

	#generate the histograms and store them in the dictionary
	for samp in masks:
		nofz[f'nz_{samp}'] = np.histogram(z_mc[masks[samp]], bins=bins, density=True)[0]
	
	return nofz


def get_tracers(nofz, cosmo):
	'''
	Creates and returns a dictionary of NumberCountsTracer objects for
	each tomographic bin.

	Parameters
	----------
	nofz: dict
		Dictionary containing n(z) distributions for each bin and the
		redshifts at which they are defined.
	
	cosmo: ccl.Cosmology
		Fiducial cosmology object.
	
	Returns
	-------
	tracers: dict
		Dictionary containing tracer information for each bin.
	'''

	#create a dictionary containing the tracers in each redshift bin
	tracers = {
		k : ccl.NumberCountsTracer(
											cosmo, 
											has_rsd=False, 
											dndz=(nofz['z'], nofz[f'nz_{k}']), 
											bias=(nofz['z'], np.ones_like(nofz['z']))
											)
		for k in cf.samples
	}

	return tracers


def get_theory_cells(tracers, pairings, ells, cosmo, pk):
	'''
	Computes theoretical predictions for the angular power spectra for the 
	specified pairs of tomographic bins.

	Parameters
	----------
	tracers: dict
		Dictionary containing NumberCountsTracer objects for each tomographic
		bin.
	
	pairings: list
		List of tuples, with each tuple containing the labels of the bins
		being paired.
	
	ells: array-like
		Multipoles at which the theory C_ells will be defined.
	
	cosmo: ccl.Cosmology
		Fiducial cosmology.
	
	pk: ccl.halos.halomod_Pk2D
		Galaxy halo-model power spectrum.
	
	Returns
	-------
	theory_cells: dict
		Dictionary containing the theoretical angular power spectra for each
		bin pair, as well as the multipoles at which they are defined.

	'''
	theory_cells = {'ells' : ells}
	for i,j in pairings:
		c_ell = ccl.angular_cl(
							cosmo,
							tracers[i],
							tracers[j],
							ells,
							p_of_k_a=pk
						)
		theory_cells[f'{i}-{j}'] = c_ell
	
	return theory_cells


#######################################################
###############    START OF SCRIPT    #################
#######################################################

#retrieve the bin pairings
pairings, _, pairings_s = cu.get_bin_pairings(cf.nsamples, labels=[k for k in cf.samples])

#define the fiducial cosmology
cosmo = ccl.Cosmology(**cf.cosmo_fiducial)

#range of wavenumbers and scale factors over which theory power spectra will be computed
lk_arr = np.log(np.geomspace(1E-4, 100, 256))
a_arr = 1. / (1. + np.linspace(0, 6, 100)[::-1])

#theory c_ell settings
lmax = 3. * cf.nside_hi - 1
ells = np.unique(np.geomspace(1, lmax, 128).astype(int)).astype(float)

#create a halo model
m200def = ccl.halos.MassDef200m							#halo mass definition
hmf = ccl.halos.MassFuncTinker08(mass_def=m200def)		#halo mass function
hbf = ccl.halos.HaloBiasTinker10(mass_def=m200def)		#halo bias function
conc = ccl.halos.ConcentrationDuffy08(mass_def=m200def)	#halo concentration function
#feed this information to a HaloMassCalculator
hmc = ccl.halos.HMCalculator(
	mass_function=hmf,
	halo_bias=hbf,
	mass_def=m200def
	)
#create halo profile
prof = ccl.halos.HaloProfileHOD(mass_def=m200def, concentration=conc)
#2pt correlator
prof2pt = ccl.halos.Profile2ptHOD()

#halo-model power spectrum for galaxies
pk = ccl.halos.halomod_Pk2D(
	cosmo,
	hmc,
	prof,
	prof_2pt=prof2pt,
	prof2=prof,
	a_arr=a_arr,
	lk_arr=lk_arr
)

#if using DIR outputs, load them now
if cf.use_dir:
	print('Retrieving DIR n(z) distributions...')
	with h5py.File(cf.nofz_files.nz_dir, 'r') as hf:
		nofz = {k : hf[k][:] for k in hf.keys()}
	#create a dictionary containing the tracers in each redshift bin
	tracers = get_tracers(nofz, cosmo)
	print('Computing theory C_ells...')
	#compute theory C_ells
	theory_cells = get_theory_cells(tracers, pairings_s, ells, cosmo, pk)
	
#cycle through the specified fields
for fd in cf.fields:
	print(colour_string(fd.upper(), 'orange'))
	if not cf.use_dir:
		print('Computing n(z) distributions from Monte-Carlo draws...')
		#compute estimates of the n(z) distributions in each bin
		nofz = compute_nofz(fd)
		#save the n(z) info to a file
		outfile = f'{cf.paths.out}{fd}/{cf.nofz_files.nz_mc}'
		with h5py.File(outfile, 'w') as hf:
			for k in nofz.keys():
				hf.create_dataset(k, data=nofz[k])
		#create a dictionary containing the tracers in each redshift bin
		tracers = get_tracers(nofz, cosmo)
		print('Computing theory C_ells...')
		#compute theory C_ells
		theory_cells = get_theory_cells(tracers, pairings_s, ells, cosmo, pk)

	print('Saving theory C_ells...')
	#open the output file, compute and save the theory cells
	outfile = f'{cf.paths.out}{fd}/{cf.cell_files.theory}' 
	with h5py.File(outfile, 'w') as psfile:
		for k in theory_cells.keys():
			psfile.create_dataset(k, data=theory_cells[k])
	

