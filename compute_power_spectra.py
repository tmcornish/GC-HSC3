#####################################################################################################
# - Uses NaMaster to compute power spectra from the galaxy delta_g maps, deprojecting any 
#   systematics templates in the process.
# - TODO: ensure that script doesn't use previous output if deprojection is to occur and didn't on previous
#	run.
#####################################################################################################

import config
import healpy as hp
import healsparse as hsp
import numpy as np
from map_utils import *
import h5py
import pymaster as nmt
from matplotlib import pyplot as plt
import plot_utils as pu
import itertools
from output_utils import colour_string
import os
import glob

import faulthandler
faulthandler.enable()
### SETTINGS ###
cf = config.computePowerSpectra
plt.style.use(pu.styledict)


###################
#### FUNCTIONS ####
###################


def load_map(map_path, apply_mask=False, is_systmap=False, mask=None):
	'''
	Loads an individual HealSparse map and returns their pixel values in healPIX 
	RING ordering. If told to, will also multiply the map by the mask and/or 
	calculate the mean of the map and subtract it from all pixels. Both of these
	operations require the mask (in full HealPIX format) as input. 


	Parameters
	----------
	map_path: str
		Path to the map being read.

	apply_mask: bool
		If True, will perform element-wise multiplication by the mask (given as separate input).

	is_systmap: bool
		If True, subtracts the mean from the value of all pixels (necessary for systematics maps).
	
	mask: MaskData
		Object containing the mask and any potentially relevant pre-computed values.

	Returns
	-------
	fs_map: np.array
		Full-sky map data (RING ordering).
	'''

	#initialise an empty full-sky map (NOTE: can take a lot of memory for high nside_sparse)
	fs_map = hsp.HealSparseMap.read(map_path).generate_healpix_map(nest=False)
	fs_map[fs_map == hp.UNSEEN] = 0.

	if is_systmap:
		if mask is not None:
			mu = np.sum(fs_map[mask.vpix] * mask.mask[mask.vpix]) / mask.sum
			fs_map[mask.vpix] -= mu
		else:
			print('Could not correct systematics map; no MaskData provided.')
	
	if apply_mask:
		if mask is not None:
			fs_map *= mask.mask
		else:
			print('Could not apply mask to map; no MaskData provided.')
		

	return fs_map


def load_tomographic_maps(map_path, apply_mask=False, mask=None):
	'''
	Loads files containing maps split into tomographic bins (e.g. delta_g) and
	returns their pixel values in healPIX RING ordering. If told to, will also 
	multiply the map by the mask, in which case a MaskData object is required
	as input. 


	Parameters
	----------
	map_path: str
		Path to the map being read.

	apply_mask: bool
		If True, will perform element-wise multiplication by the mask (given as separate input).

	mask: MaskData
		Object containing the mask and any potentially relevant pre-computed values.

	Returns
	-------
	fs_maps: list
		List containing full-sky data (RING ordering) for each tomographic map.
	'''
	#empty list in which the full-sky maps will be stored
	fs_maps = []

	#load the HealSparse file
	hsp_map = hsp.HealSparseMap.read(map_path)

	#cycle through the maps
	for d in hsp_map.dtype.names:
		#create full-sky realisation of the map
		fs_map = hsp_map[d].generate_healpix_map(nest=False)
		fs_map[fs_map == hp.UNSEEN] = 0.
		
		#multiply by the mask if told to do so
		if apply_mask:
			if mask is not None:
				fs_map *= mask.mask
			else:
				print('Could not apply mask to map; no MaskData provided.')
		
		#append the full-sky map to the list
		fs_maps.append(fs_map)
	
	return fs_maps






#######################################################
###############    START OF SCRIPT    #################
#######################################################

#maximum ell allowed by the resolution
ell_max = 3 * cf.nside_hi
#get pixel area in units of steradians
Apix = hp.nside2pixarea(cf.nside_hi)
#get the number of pixels in a full-sky map at the required resolution
npix = hp.nside2npix(cf.nside_hi)


if cf.use_N19_bps:
	#retrieve bandpower edges from config
	bpw_edges = np.array(cf.bpw_edges).astype(int)
	#only include bandpowers < 3 * NSIDE
	bpw_edges = bpw_edges[bpw_edges <= ell_max]
else:
	if cf.log_spacing:
		bpw_edges = np.geomspace(cf.ell_min, ell_max, cf.nbpws).astype(int)
	else:
		bpw_edges = np.linspace(cf.ell_min, ell_max, cf.nbpws).astype(int)
#create pymaster NmtBin object using these bandpower objects
b = nmt.NmtBin.from_edges(bpw_edges[:-1], bpw_edges[1:])


#get the effective ells
ell_effs = b.get_effective_ells()

#retrieve the number of redshift bins
nbins = len(cf.zbins) - 1
#also get all possible pairings of bins
l = list(range(nbins))
pairings = [i for i in itertools.product(l,l) if tuple(reversed(i)) >= i]


#cycle through the fields being analysed (TODO: later change to global fields)
for fd in cf.get_global_fields():
	print(colour_string(fd.upper(), 'orange'))

	#path to the directory containing the maps
	PATH_MAPS = f'{cf.PATH_OUT}{fd}/'

	#set up a pymaster Workspace object
	w = nmt.NmtWorkspace()
	#create a variable assignment that will later be occupied by a CovarianceWorkspace
	cw = nmt.NmtCovarianceWorkspace()

	PATH_CACHE = PATH_MAPS + 'cache/'
	#see if directory for cached workspaces exists; make it if not
	if not os.path.exists(PATH_CACHE):
		os.system(f'mkdir -p {PATH_CACHE}')
	
	#path to directory containing systematics maps
	PATH_SYST = f'{PATH_MAPS}systmaps/'
	#check for 'All' in systmaps and convert this to a list of all systematics maps
	if 'all' in map(str.lower, cf.systs):
		cf.systs = [os.path.basename(m) for m in (glob.glob(f'{PATH_SYST}*_{cf.nside_hi}.hsp') + glob.glob(f'{PATH_SYST}*_{cf.nside_hi}_*.hsp'))]
	
	#if given a max number of systematics to deproject, slice the list accordingly
	if cf.Nsyst_max is not None:
		cf.systs = cf.systs[:cf.Nsyst_max]

	#file containing list of systematics maps deprojected in the previous run
	deproj_file = PATH_CACHE + cf.deproj_file
	if os.path.exists(deproj_file):
		with open(deproj_file, 'r+') as df:
			#see which (if any) systematics have been deprojected previously
			deproj_done = df.read().split('\n')
			#see if this is the same as the list specified in the config file (accounting for different ordering)
			if sorted(deproj_done) == sorted(cf.systs):
				calc = False
				print('Same systematics maps provided')
			else:
				calc = True
				print('Different systematics maps provided')
				#write the list of provided systematics to the file
				df.seek(0)
				df.truncate()
				df.write('\n'.join(cf.systs))
	else:
		if len(cf.systs) == 0:
			calc = False
			print('No systematics provided')
		else:
			calc = True
			with open(deproj_file, 'w') as df:
				df.write('\n'.join(cf.systs))
		

	#see if workspaces have already been created from a previous run
	wsp_path = PATH_CACHE + cf.wsp_file
	covwsp_path = PATH_CACHE + cf.covwsp_file
	if os.path.exists(wsp_path) and not calc:
		w.read_from(wsp_path)
	else:
		calc = True
	if os.path.exists(covwsp_path) and not calc:
		cw.read_from(covwsp_path)
	else:
		calc = True
		

	#load the delta_g maps
	deltag_maps = load_tomographic_maps(PATH_MAPS + cf.deltag_maps)

	#load the survey mask and convert to full-sky realisation
	mask = MaskData(PATH_MAPS + cf.survey_mask, mask_thresh=cf.weight_thresh)


	print('Loading systematics maps...')
	if len(cf.systs) > 0:
		#load the systematics maps and convert to full-sky realisations
		systmaps = [load_map(PATH_SYST + s, is_systmap=True, mask=mask) for s in cf.systs]
		#reshape the resultant list to have dimensions (nsyst, 1, npix)
		nsyst = len(systmaps)
		systmaps = np.array(systmaps).reshape([nsyst, 1, npix])
		deproj = True
		print('templates: ', np.mean(systmaps))
	else:
		systmaps = None
		deproj = False
	print('Done!')


	print('Creating NmtFields...')
	density_fields = [nmt.NmtField(mask.mask, [d], templates=systmaps, lite=cf.lite) for d in deltag_maps]
	print('Done!')

	#delete the systematics and delta_g maps to clear some memory
	del systmaps
	del deltag_maps

	

	#retrieve the IDs of pixels above the mask threshold, as this is all that is
	#required from the mask henceforth
	above_thresh = mask.vpix
	sum_w_above_thresh = mask.sum
	mu_w = mask.mean
	mu_w2 = mask.meansq
	del mask
	
	#load the N_g maps and calculate the mean weighted by the mask
	mu_N_all = [nmap[above_thresh].sum() / sum_w_above_thresh for nmap in load_tomographic_maps(PATH_MAPS + cf.ngal_maps)]


	#full path to the output file
	outfile = f'{cf.PATH_OUT}{fd}/{cf.outfile}'
	#open the file, creating it if it doesn't exist
	with h5py.File(outfile, mode='r+') as psfile:
		
		#cycle through all possible pairings of redshift bins
		for ip,p in enumerate(pairings):
			i,j = p

			#see if a group for the current pairing already exists
			p_str = str(p)
			print(colour_string(p_str, 'green'))

			gp = psfile.require_group(p_str)

			f_i = density_fields[i]
			f_j = density_fields[j]
			cl_coupled = nmt.compute_coupled_cell(f_i, f_j)

			#use these along with the mask to get a guess of the true C_ell
			cl_guess = cl_coupled / mu_w2

			if ip == 0 and calc:
				#compute the mode coupling matrix (only need to compute once since same mask used for everything)
				print('Computing mode coupling matrix...')
				w.compute_coupling_matrix(f_i, f_j, b)
				print('Done!')

				print('Calculating coupling coefficients...')
				#compute coupling coefficients
				cw.compute_coupling_coefficients(f_i, f_j)
				print('Done!')
			else:
				print('Using coupling matrix and coefficients from cache.')

			#only calculate bias-related quantities if templates have been provided
			if deproj and not cf.lite:
				if calc:
					print('Calculating deprojection bias...')
					#compute the deprojection bias
					cl_bias = nmt.deprojection_bias(f_i, f_j, cl_guess)
					print('Done!')
				else:
					print('Combination of systematics matches previous run; using cached results.')
					cl_bias = gp['cl_bias'][:]
			else:
				print('No systematics maps provided; skipping deprojection bias calculation.')
				cl_bias = np.zeros_like(cl_guess)

			#multiplicative correction to delta_g of (1 / (1-Fs)) due to stars results in factor of (1 / (1 - Fs))^2 correction to Cl
			if cf.correct_for_stars:
				mult = (1 / (1 - cf.Fs_fiducial)) ** 2.
				cl_coupled *= mult
				cl_guess *= mult

			#compute the decoupled C_ell (w/o deprojection)
			cl_decoupled = w.decouple_cell(cl_coupled)
			#compute the decoupled C_ell (w/ deprojection)
			cl_decoupled_debiased = w.decouple_cell(cl_coupled, cl_bias=cl_bias)
			#decouple the bias C_ells as well
			cl_bias_decoupled = w.decouple_cell(cl_bias)


			########################
			# NOISE POWER SPECTRUM #
			########################

			#Only calculate for autocorrelations
			if i == j:
				mu_N = mu_N_all[i]
				#calculate the noise power spectrum
				N_ell_coupled = np.full(ell_max, Apix * mu_w / mu_N).reshape((1,ell_max))
				#decouple
				N_ell_decoupled = w.decouple_cell(N_ell_coupled)

			
			#######################
			# GAUSSIAN COVARIANCE #
			#######################

			#extract (gaussian) covariance matrix
			print('Calculating covariance matrix...')
			n_ell = len(cl_decoupled[0])
			covar = nmt.gaussian_covariance(cw, 
											0, 0, 0, 0,			#spin of each field
											[cl_guess[0]],	
											[cl_guess[0]],
											[cl_guess[0]],
											[cl_guess[0]],
											w)
			#errorbars for each bandpower
			err_cell = np.diag(covar) ** 0.5
			print('Done!')

			##################
			# SAVING RESULTS #
			##################

			#populate the output file with the results
			_ = gp.require_dataset('ell_effs', shape=ell_effs.shape, dtype=ell_effs.dtype, data=ell_effs)
			_ = gp.require_dataset('cl_coupled', shape=cl_coupled.shape, dtype=cl_coupled.dtype, data=cl_coupled)
			_ = gp.require_dataset('cl_decoupled', shape=cl_decoupled.shape, dtype=cl_decoupled.dtype, data=cl_decoupled)
			_ = gp.require_dataset('cl_guess', shape=cl_guess.shape, dtype=cl_guess.dtype, data=cl_guess)
			_ = gp.require_dataset('N_ell_coupled', shape=N_ell_coupled.shape, dtype=N_ell_coupled.dtype, data=N_ell_coupled)
			_ = gp.require_dataset('N_ell_decoupled', shape=N_ell_decoupled.shape, dtype=N_ell_decoupled.dtype, data=N_ell_decoupled)
			_ = gp.require_dataset('covar', shape=covar.shape, dtype=covar.dtype, data=covar)
			_ = gp.require_dataset('err_cell', shape=err_cell.shape, dtype=err_cell.dtype, data=err_cell)
			_ = gp.require_dataset('cl_bias', shape=cl_bias.shape, dtype=cl_bias.dtype, data=cl_bias)
			_ = gp.require_dataset('cl_bias_decoupled', shape=cl_bias_decoupled.shape, dtype=cl_bias_decoupled.dtype, data=cl_bias_decoupled)
			_ = gp.require_dataset('cl_decoupled_debiased', shape=cl_decoupled_debiased.shape, dtype=cl_decoupled_debiased.dtype, data=cl_decoupled_debiased)


	######################
	# CACHING WORKSPACES #
	######################

	#write the workspaces to the cache directory
	w.write_to(f'{PATH_CACHE}{cf.wsp_file}')
	cw.write_to(f'{PATH_CACHE}{cf.covwsp_file}')
		



