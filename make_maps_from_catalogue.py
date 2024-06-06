#####################################################################################################
# - Creates the following maps and masks using HealSparse:
#	- dust attenuation in each band
#	- star counts
#	- binary bright object mask
#	- masked fraction map
#	- depth map
#	- survey mask
#####################################################################################################

import os
import config
import healpy as hp
import healsparse as hsp
from astropy.table import Table
import numpy as np
from output_utils import colour_string
from map_utils import *
import h5py
from functools import reduce

### SETTINGS ###
cf = config.makeMapsFromCat


###################
#### FUNCTIONS ####
###################


def makeDustMap(cat, group='', band='i'):
	'''
	Creates dust maps for each of the specified bands.

	Paameters
	---------
	cat: h5py.File object
		Catalogue containing (at least): RAs, Decs, and dust attenuation values at each of these
		coordinates in each of the specified bands.

	group: str
		Group within which the relevant data are expected to reside.

	Returns
	-------
	dust_maps: recarray
		recarray for which each entry is a dust attenuation map in each of the specified bands.
	'''

	print(f'Creating dust map (band {band})...')

	#cycle through the bands and calculate the mean dust attenuation in each pixel
	dust_map, _ = createMeanStdMap(cat[f'{group}/ra'][:], cat[f'{group}/dec'][:], cat[f'{group}/a_{band}'][:], cf.nside_lo, cf.nside_hi)

	return dust_map


def makeBOMask(cat, group=''):
	'''
	Creates bright object mask.

	Paameters
	---------
	cat: h5py.File object
		Catalogue containing (at least): RAs, Decs.

	group: str
		Group within which the relevant data are expected to reside.

	Returns
	-------
	bo_mask: HealSparseMap
		HealSparse map containing 0s at the masked positions and 1 everywhere else.
	'''

	print('Creating bright object mask...')
	#get the RAs and Decs of all sources in the catalogue
	ra = cat[f'{group}/ra'][:]
	dec = cat[f'{group}/dec'][:]
	#get the columns containing bright-object flags
	flags = [cat[f'{group}/{flag_col}'][:] for flag_col in cf.bo_flags]

	bo_mask = createMask(ra, dec, flags, cf.nside_lo, cf.nside_hi)

	return bo_mask


def makeMaskedFrac(cat, group=''):
	'''
	Creates a map showing the fraction of each pixel that is maske by the bright object criteria.

	Paameters
	---------
	cat: h5py.File object
		Catalogue containing (at least): RAs, Decs.

	group: str
		Group within which the relevant data are expected to reside.

	Returns
	-------
	mf_map: HealSparseMap
		HealSparse map containing the fraction of each pixel that is masked.
	'''

	print('Creating masked fraction map...')
	#get the RAs and Decs of all sources in the catalogue
	ra = cat[f'{group}/ra'][:]
	dec = cat[f'{group}/dec'][:]
	#get the columns containing bright-object flags
	flags = [cat[f'{group}/{flag_col}'][:] for flag_col in cf.bo_flags]
	#add all the masks together
	add = lambda x,y : x+y
	flagged = reduce(add, flags)

	#create counts map of all sources
	Ntotal_map = pixelCountsFromCoords(ra, dec, cf.nside_lo, cf.nside_hi)
	vpix_total = Ntotal_map.valid_pixels
	#create counts map of flagged sources
	Nflagged_map = pixelCountsFromCoords(ra[flagged], dec[flagged], cf.nside_lo, cf.nside_hi)
	vpix_flagged = Nflagged_map.valid_pixels
	#identify pixels that only contain unflagged sources
	vpix_unflagged_only = np.array(list(set(vpix_total) - set(vpix_flagged)))
	Nflagged_map[vpix_unflagged_only] = np.zeros(len(vpix_unflagged_only), dtype=np.int32)
	#calculate the fraction of masked sources in each pixel
	mf_map = hsp.HealSparseMap.make_empty(cf.nside_lo, cf.nside_hi, dtype=np.float64)
	mf_map[vpix_total] = Nflagged_map[vpix_total] / Ntotal_map[vpix_total]

	return mf_map


def makeStarMap(cat, group=''):
	'''
	Creates map showing the number of stars in each pixel.

	Paameters
	---------
	cat: h5py.File object
		Catalogue containing (at least): RAs and Decs of each star detected in the field.

	group: str
		Group within which the relevant data are expected to reside.

	Returns
	-------
	star_map: HealSparseMap
		HealSparse map containing the number of stars in each pixel.
	'''
	print('Creating star counts map...')
	#get the RAs and Decs of all sources in the catalogue
	ra = cat[f'{group}/ra'][:]
	dec = cat[f'{group}/dec'][:]

	#count the stars in each pixel
	star_map = pixelCountsFromCoords(ra, dec, cf.nside_lo, cf.nside_hi)

	return star_map


def makeDepthMap(cat, group='', stars_only=True):
	'''
	Creates map showing the number of stars in each pixel.

	Paameters
	---------
	cat: h5py.File object
		Catalogue containing (at least): RAs and Decs of each star detected in the field.

	group: str
		Group within which the relevant data are expected to reside.

	stars_only: bool
		Whether to just use stars for the calculation of the depth.

	Returns
	-------
	depth_map: HealSparseMap
		HealSparse map containing the average N-sigma depth in each pixel, where N is the
		SNR threshold of the primary band (set in the config file).
	'''
	print('Creating depth map...')
	'''
	#begin by calculating SNR of each source in the primary band
	snrs = cat[f'{group}/{cf.band}_cmodel_flux'][:] / cat[f'{group}/{cf.band}_cmodel_fluxerr'][:]
	'''
	#if told to use stars only, use flags to identify which sources are stars
	if stars_only:
		try:
			star_mask = cat[f'{group}/is_star'][:]
		except KeyError:
			print(colour_string('Error: '), f'No dataset "{group}/is_star" found. Using all sources instead.')
			star_mask = np.full(len(cat[f'{group}/ra']), True)
	else:
		star_mask = np.full(len(cat[f'{group}/ra']), True)

	#get the RAs and Decs of all sources in the catalogue
	ra = cat[f'{group}/ra'][star_mask]
	dec = cat[f'{group}/dec'][star_mask]


	#retrieve the flux error in the primary band for each source
	fluxerr = cat[f'{group}/{cf.band}_cmodel_fluxerr'][star_mask]
	#retrieve the SNR threshold
	snr_thresh = int(cf.sn_pri)

	#create a map containing the mean fluxerr multiplied by the SNR threshold
	depth_map, _ = createMeanStdMap(ra, dec, snr_thresh * fluxerr, cf.nside_lo, cf.nside_hi)
	vpix = depth_map.valid_pixels
	#fluxes and associated errors are given in nJy - convert to AB mags
	depth_map[vpix] = -2.5 * np.log10(depth_map[vpix] * 10. ** (-9.)) + 8.9

	return depth_map


def makeSurveyMask(mf_map, depth_map=None):
	'''
	Creates a binary mask from the masked fraction map by applying a threshold for the
	masked fraction.

	Parameters
	----------
	mf_map: HealSparseMap
		Masked fraction map.

	depth_map: HealSparseMap or None
		(Optional) Map of the survey depth. If provided, will additionally set to 0 the
		weight of any pixels below the depth threshold specified in the config file. 

	Returns
	-------
	mask: HealSparseMap
		Binary mask.
	'''
	print('Creating survey mask...')
	#make an empty map with the same properties as the masked fraction map
	mask = hsp.HealSparseMap.make_empty(mf_map.nside_coverage, mf_map.nside_sparse, dtype=np.float64)
	#identify valid pixels
	vpix = mf_map.valid_pixels

	#fill the survey mask with 1 minus the masked fraction at the relevant pixels
	mask[vpix] = 1. - mf_map[vpix]

	#mask pixels below the depth threshold if a depth map is provided
	if depth_map is not None:
		vpix_dm = depth_map.valid_pixels
		vpix_shallow = vpix_dm[depth_map[vpix_dm] < cf.depth_cut]
		mask[vpix_shallow] = 0.

	return mask


#######################################################
###############    START OF SCRIPT    #################
#######################################################

#cycle through each of the fields
for fd in cf.get_global_fields():
	print(colour_string(fd.upper(), 'orange'))
	#output directory for this field
	OUT = cf.PATH_OUT + fd
	#path for systematics maps (create if it doesn't exist already)
	PATH_SYST = OUT + '/systmaps'
	if not os.path.exists(PATH_SYST):
		os.system(f'mkdir -p {PATH_SYST}')

	#load the basic and fully cleaned galaxy/star catalogues for this field
	cat_basic = h5py.File(f'{OUT}/{cf.cat_basic}', 'r')
	cat_main = h5py.File(f'{OUT}/{cf.cat_main}', 'r')
	cat_stars = h5py.File(f'{OUT}/{cf.cat_stars}', 'r')

	for b,dm in zip(cf.bands, cf.dustmaps):
		#make the dust maps in the current band
		dust_map = makeDustMap(cat_basic, group='photometry', band=b)
		#write to a file
		dust_map.write(f'{PATH_SYST}/{dm}', clobber=True)

	#make the bright object mask
	bo_mask = makeBOMask(cat_basic, group='photometry')
	#write to a file
	bo_mask.write(f'{OUT}/{cf.bo_mask}', clobber=True)
	healsparseToHDF(bo_mask, f'{OUT}/{cf.bo_mask[:-4]}.hdf5', group='maps/mask')

	#make the masked fraction map
	mf_map = makeMaskedFrac(cat_basic, group='photometry')
	#write to a file
	mf_map.write(f'{OUT}/{cf.masked_frac}', clobber=True)

	#make the star counts map
	star_map = makeStarMap(cat_stars, group='photometry')
	#write to a file
	star_map.write(f'{PATH_SYST}/{cf.star_map}', clobber=True)

	#make the depth map
	depth_map = makeDepthMap(cat_basic, group='photometry', stars_only=True)
	#write to a file
	depth_map.write(f'{OUT}/{cf.depth_map}', clobber=True)

	#make a survey mask by applying a masked fraction threshold
	survey_mask = makeSurveyMask(mf_map, depth_map=depth_map)
	#write to a file
	survey_mask.write(f'{OUT}/{cf.survey_mask}', clobber=True)
	#calculate the area above the mask threshold and the fractional sky coverage
	A_unmasked, f_sky = maskAreaSkyCoverage(survey_mask, thresh=cf.weight_thresh)
	mask_meta = {'area' : A_unmasked, 'f_sky': f_sky}
	healsparseToHDF(survey_mask, f'{OUT}/{cf.survey_mask[:-4]}.hdf5', group='maps/mask', metadata=mask_meta)