#####################################################################################################
# - Uses NaMaster to compute power spectra from the galaxy delta_g maps, deprojecting any 
#   systematics templates in the process.
#####################################################################################################

import os
import sys
import healpy as hp
import numpy as np
import map_utils as mu
import cell_utils as cu
import h5py
import pymaster as nmt
from output_utils import colour_string
import glob
from configuration import PipelineConfig as PC

import faulthandler
faulthandler.enable()
### SETTINGS ###
config_file = sys.argv[1]
cf = PC(config_file, stage='computePowerSpectra')

if cf.platform == 'nersc':
	os.system(f'taskset -pc 0-255 {os.getpid()}')

#############################
######### FUNCTIONS #########
#############################

def make_density_fields(deproj_file, systs, idx=None):
	if os.path.exists(deproj_file):
		with open(deproj_file, 'r+') as df:
			#see which (if any) systematics have been deprojected previously
			deproj_done = df.read().split('\n')
			#see if this is the same as the list specified in the config file (accounting for different ordering)
			if sorted(deproj_done) == sorted(systs):
				print(f'Same systematics maps provided; skipping all calculations for field {fd}')
				return None, None, None
			else:
				print('Different systematics maps provided')
				#write the list of provided systematics to the file
				df.seek(0)
				df.truncate()
				df.write('\n'.join(systs))
	else:
		if len(systs) == 1:
			print('No systematics provided')
		else:
			with open(deproj_file, 'w') as df:
				df.write('\n'.join(systs))
		

	#load the delta_g maps
	deltag_maps = mu.load_tomographic_maps(PATH_MAPS + cf.maps.deltag_maps, idx=idx)


	print('Loading systematics maps...')
	if len(systs) > 1:
		#load the systematics maps and convert to full-sky realisations
		systmaps = [mu.load_map(PATH_SYST + s, is_systmap=True, mask=mask) for s in systs[:-1]]
		#reshape the resultant list to have dimensions (nsyst, 1, npix)
		nsyst = len(systmaps)
		systmaps = np.array(systmaps).reshape([nsyst, 1, npix])
		print('templates: ', np.mean(systmaps))
	else:
		systmaps = None
		nsyst = 0
	print('Done!')

	print('Creating NmtFields (without deprojection)...')
	density_fields_nd = [nmt.NmtField(mask.mask_full, [d], templates=None, lite=cf.lite) for d in deltag_maps]
	print('Done!')
	if systmaps is None:
		return density_fields_nd, density_fields_nd, 0

	print('Creating NmtFields...')
	density_fields = [nmt.NmtField(mask.mask_full, [d], templates=systmaps, lite=cf.lite) for d in deltag_maps]
	print('Done!')

	#delete the systematics and delta_g maps to clear some memory
	del systmaps
	del deltag_maps

	return density_fields, density_fields_nd, nsyst


def compute_covariance(w, cw, f_i1, f_i2, f_j1=None, f_j2=None, return_cl_coupled=False, return_cl_guess=False):
	#see if additional fields have been provided for the second power spectrum
	if f_j1 is None:
		f_j1 = f_i1
	if f_j2 is None:
		f_j2 = f_i2
	
	#compute coupled c_ells for each possible combination of i and j
	cl_coupled_i1j1 = nmt.compute_coupled_cell(f_i1, f_j1)
	cl_coupled_i1j2 = nmt.compute_coupled_cell(f_i1, f_j2)
	cl_coupled_i2j1 = nmt.compute_coupled_cell(f_i2, f_j1)
	cl_coupled_i2j2 = nmt.compute_coupled_cell(f_i2, f_j2)
	#use these along with the mask to get a guess of the true C_ell
	cl_guess_i1j1 = cl_coupled_i1j1 / mu_w2
	cl_guess_i1j2 = cl_coupled_i1j2 / mu_w2
	cl_guess_i2j1 = cl_coupled_i2j1 / mu_w2
	cl_guess_i2j2 = cl_coupled_i2j2 / mu_w2


	covar = nmt.gaussian_covariance(cw, 
									0, 0, 0, 0,			#spin of each field
									[cl_guess_i1j1[0]],	
									[cl_guess_i1j2[0]],
									[cl_guess_i2j1[0]],
									[cl_guess_i2j2[0]],
									w)
	#errorbars for each bandpower
	err_cell = np.diag(covar) ** 0.5

	to_return = [covar, err_cell]
	if return_cl_coupled:
		to_return.append([cl_coupled_i1j1, cl_coupled_i1j2, cl_coupled_i2j1, cl_coupled_i2j2])
	if return_cl_guess:
		to_return.append([cl_guess_i1j1, cl_guess_i1j2, cl_guess_i2j1, cl_guess_i2j2])


	return (*to_return,)



#######################################################
###############    START OF SCRIPT    #################
#######################################################

#retrieve the pairing being analysed from the arguments if provided
try:
	pairings = [sys.argv[2]]
	per_tomo = True
except IndexError:
	_, pairings = cu.get_bin_pairings(cf.nsamples)
	per_tomo = False

#maximum ell allowed by the resolution
ell_max = 3 * cf.nside_hi
#get pixel area in units of steradians
Apix = hp.nside2pixarea(cf.nside_hi)
#get the number of pixels in a full-sky map at the required resolution
npix = hp.nside2npix(cf.nside_hi)

#set the bandpower edges
bpw_edges = cu.get_bpw_edges(ell_max, ell_min=cf.ell_min, nbpws=cf.nbpws, spacing=cf.bpw_spacing)
#create pymaster NmtBin object using these bandpower objects
b = nmt.NmtBin.from_edges(bpw_edges[:-1], bpw_edges[1:])


#get the effective ells
ell_effs = b.get_effective_ells()


#cycle through the fields being analysed
for fd in cf.fields:
	print(colour_string(fd.upper(), 'orange'))

	#path to the directory containing the maps
	PATH_MAPS = f'{cf.paths.out}{fd}/'
	#path to the file containing theory predictions
	theory_file = PATH_MAPS + cf.cell_files.theory
	theory_exists = os.path.exists(theory_file)
	if theory_exists:
		with h5py.File(theory_file, 'r') as psfile:
			theory_keys = list(psfile.keys())

	#load the survey mask and convert to full-sky realisation
	mask = mu.MaskData(PATH_MAPS + cf.maps.survey_mask)
	#retrieve relevant quantities from the mask data
	above_thresh = mask.vpix_ring
	sum_w_above_thresh = mask.sum
	mu_w = mask.mean
	mu_w2 = mask.meansq

	#set up a pymaster Workspace object
	w = nmt.NmtWorkspace()
	#create a variable assignment that will later be occupied by a CovarianceWorkspace
	cw = nmt.NmtCovarianceWorkspace()

	PATH_CACHE = PATH_MAPS + 'cache/'
	#see if directory for cached workspaces exists; make it if not
	if not os.path.exists(PATH_CACHE):
		os.system(f'mkdir -p {PATH_CACHE}')
	
	#see if workspaces have already been created from a previous run
	wsp_path = PATH_CACHE + cf.cache_files.workspaces.wsp
	covwsp_path = PATH_CACHE + cf.cache_files.workspaces.covwsp
	if os.path.exists(wsp_path):
		w.read_from(wsp_path)
		calc = False
	else:
		calc = True
	if os.path.exists(covwsp_path):
		cw.read_from(covwsp_path)
		calc |= False
	else:
		calc |= True
	
	#path to directory containing systematics maps
	PATH_SYST = f'{PATH_MAPS}systmaps/'
	systs = []
	#check for 'All' in systmaps and convert this to a list of all systematics maps
	if 'all' in map(str.lower, cf.systs):
		systs = [os.path.basename(m) for m in glob.glob(f'{PATH_SYST}*_nside{cf.nside_hi}*.hsp')]
	
	#if given a max number of systematics to deproject, slice the list accordingly
	if cf.Nsyst_max is not None:
		systs = systs[:cf.Nsyst_max]
	
	#add the boolean 'lite' to the end of the list of systematics
	systs.append(str(cf.lite))

	#file containing list of systematics maps deprojected in the previous run
	deproj_file = PATH_CACHE + cf.cache_files.deproj.deprojected
	if not per_tomo:
		density_fields, density_fields_nd, nsyst = make_density_fields(deproj_file, systs)
		if density_fields is None:
			continue
	
	#set up dictionary for recording whether deprojection coefficients have been saved
	alphas_saved = {i : False for i in range(cf.nsamples)}

	#full path to the output file
	outfile_main = f'{PATH_MAPS}{cf.cell_files.main}'	
	for p in pairings:
		i,j = [int(x) for x in p.strip('()').split(',')]
		label_i = list(cf.samples)[i]
		label_j = list(cf.samples)[j]
		outfile_now = f'{outfile_main[:-5]}_{i}_{j}.hdf5'

		print(colour_string(p, 'green'))

		if per_tomo:
			deproj_file = f'{deproj_file[:-4]}_{i}_{j}.txt'
			density_fields, density_fields_nd, nsyst = make_density_fields(deproj_file, systs, idx=[i,j])
			if density_fields is None:
				continue
			f_i, f_j = density_fields
			f_i_nd, f_j_nd = density_fields_nd
		else:
			f_i = density_fields[i]
			f_j = density_fields[j]
			f_i_nd = density_fields_nd[i]
			f_j_nd = density_fields_nd[j]


		if calc:
			#compute the mode coupling matrix (only need to compute once since same mask used for everything)
			print('Computing mode coupling matrix...')
			w.compute_coupling_matrix(f_i, f_j, b)
			print('Done!')
			#write the workspace to the cache directory
			w.write_to(f'{PATH_CACHE}{cf.cache_files.workspaces.wsp}')

			print('Calculating coupling coefficients...')
			#compute coupling coefficients
			cw.compute_coupling_coefficients(f_i, f_j)
			#write the workspace to the cache directory
			cw.write_to(f'{PATH_CACHE}{cf.cache_files.workspaces.covwsp}')
			print('Done!')

			#set calc to False for future iterations
			calc = False
		else:
			print('Using coupling matrix and coefficients from cache.')
		
		print('Calculating covariance matrix (without deprojection)...')
		covar_nd, err_cell_nd, (_, cl_coupled_ij_nd, _, _), (_, cl_guess_ij_nd, _, _) = compute_covariance(w, cw, f_i_nd, f_j_nd,
																					return_cl_coupled=True,
																					return_cl_guess=True)
		print('Done!')

		print('Calculating covariance matrix...')
		if nsyst > 0:
			covar, err_cell, (_, cl_coupled_ij, _, _), (_, cl_guess_ij, _, _) = compute_covariance(w, cw, f_i, f_j,
																					return_cl_coupled=True,
																					return_cl_guess=True)
		else:
			covar = covar_nd
			err_cell = err_cell_nd
			cl_coupled_ij = cl_coupled_ij_nd
			cl_guess_ij = cl_guess_ij_nd
		print('Done!')


		#####################
		# DEPROJECTION BIAS #
		#####################

		#only calculate bias-related quantities if templates have been provided
		if (nsyst > 0) and not cf.lite:
			print('Calculating deprojection bias...')
			#compute the deprojection bias
			cl_bias = nmt.deprojection_bias(f_i, f_j, cl_guess_ij)
			print('Done!')
		else:
			print('No systematics maps provided; skipping deprojection bias calculation.')
			cl_bias = np.zeros_like(cl_guess_ij)
		

		#multiplicative correction to delta_g of (1 / (1-Fs)) due to stars results in factor of (1 / (1 - Fs))^2 correction to Cl
		if cf.correct_for_stars:
			mult = (1 / (1 - cf.Fs_fiducial)) ** 2.
			cl_coupled_ij *= mult
			cl_guess_ij *= mult

		#compute the decoupled C_ell (w/o debiasing)
		cl_decoupled = w.decouple_cell(cl_coupled_ij)
		#compute the decoupled C_ell (w/ deprojection)
		cl_decoupled_debiased = w.decouple_cell(cl_coupled_ij, cl_bias=cl_bias)
		#decouple the bias C_ells as well
		cl_bias_decoupled = w.decouple_cell(cl_bias)

		#compute the decoupled C_ell (w/o deprojection)
		cl_decoupled_nd = w.decouple_cell(cl_coupled_ij_nd)

		########################
		# NOISE POWER SPECTRUM #
		########################

		#Only calculate for autocorrelations
		if i == j:
			#load the N_g map and calculate the mean weighted by the mask
			mu_N = mu.load_tomographic_maps(PATH_MAPS + cf.maps.ngal_maps, idx=i)[0][above_thresh].sum() / sum_w_above_thresh
			#calculate the noise power spectrum
			N_ell_coupled = np.full(ell_max, Apix * mu_w / mu_N).reshape((1,ell_max))
			#decouple
			N_ell_decoupled = w.decouple_cell(N_ell_coupled)
		else:
			N_ell_coupled = np.zeros((1, ell_max))
			N_ell_decoupled = w.decouple_cell(N_ell_coupled)

		

		##################
		# SAVING RESULTS #
		##################
		
		with h5py.File(outfile_now, 'w') as psfile: 
			#populate the output file with the results
			gp = psfile.create_group(p)
			_ = gp.create_dataset('ell_effs', data=ell_effs)
			_ = gp.create_dataset('cl_coupled', data=cl_coupled_ij)
			_ = gp.create_dataset('cl_decoupled', data=cl_decoupled)
			_ = gp.create_dataset('cl_guess', data=cl_guess_ij)
			_ = gp.create_dataset('cl_coupled_no_deproj', data=cl_coupled_ij_nd)
			_ = gp.create_dataset('cl_decoupled_no_deproj', data=cl_decoupled_nd)
			_ = gp.create_dataset('cl_guess_no_deproj', data=cl_guess_ij_nd)
			_ = gp.create_dataset('N_ell_coupled', data=N_ell_coupled)
			_ = gp.create_dataset('N_ell_decoupled', data=N_ell_decoupled)
			_ = gp.create_dataset('covar', data=covar)
			_ = gp.create_dataset('err_cell', data=err_cell)
			_ = gp.create_dataset('covar_no_deproj', data=covar_nd)
			_ = gp.create_dataset('err_cell_no_deproj', data=err_cell_nd)
			_ = gp.create_dataset('cl_bias', data=cl_bias)
			_ = gp.create_dataset('cl_bias_decoupled', data=cl_bias_decoupled)
			_ = gp.create_dataset('cl_decoupled_debiased', data=cl_decoupled_debiased)
			#see if theoretical predictions exist for this pairing
			if theory_exists and (f'{label_i}-{label_j}' in theory_keys):
				gp['ells_theory'] = h5py.ExternalLink(theory_file, 'ells')
				gp['cl_theory'] = h5py.ExternalLink(theory_file, f'{label_i}-{label_j}')
	
		#save the best-fit coefficients for deprojection ()
		if (nsyst > 0):
			#get the coefficients
			alphas_i = f_i.alphas
			alphas_j = f_j.alphas
			if not alphas_saved[i]:
				#write to a file, with the name of each systematic
				with open(PATH_CACHE + cf.cache_files.deproj.alphas[:-4] + f'_{label_i}.txt', 'w') as alphas_file:
					alphas_file.write('Sytematic\talpha\n')
					for k in range(nsyst):
						alphas_file.write(f'{systs[k]}\t{alphas_i[k]}\n')
				alphas_saved[i] = True
			if not alphas_saved[j]:
				#write to a file, with the name of each systematic
				with open(PATH_CACHE + cf.cache_files.deproj.alphas[:-4] + f'{label_j}.txt', 'w') as alphas_file:
					alphas_file.write('Sytematic\talpha\n')
					for k in range(nsyst):
						alphas_file.write(f'{systs[k]}\t{alphas_i[k]}\n')
				alphas_saved[j] = True