#####################################################################################################
# Config file for the pHSC3 pipeline.
#####################################################################################################


################
#### GLOBAL ####
################

class cf_global:

	#relevant directories
	PATH_PIPE = '/home/cornisht/LSST_clustering/pHSC3/'			#path to pipeline scripts
	PATH_DATA = '/home/cornisht/LSST_clustering/Data/HSC_DR3/'	#where the raw data are stored
	#PATH_PIPE = '/mnt/zfsusers/tcornish/pHSC3/'
	#PATH_DATA = '/mnt/extraspace/tmcornish/Datasets/HSC_DR3/'
	PATH_OUT =  f'{PATH_PIPE}out/'												#main directory for outputs

	#data release
	dr = 'pdr3_wide'
	#fields for which the pipeline is to be run
	#fields = ['aegis']#, 'equator00', 'equator01', 'equator02', 'equator08',
				#'equator09', 'equator10', 'equator11', 'equator12', 'equator13',
				#'equator14', 'equator15', 'equator21', 'equator22', 'equator23',
				#'hectomap']
	fields = ['hectomap']

	#file containing the metadata
	metafile = f'{PATH_DATA}PDR3_WIDE_frames.fits'

	#main photometric band
	band = 'i'
	#list of all photometric bands
	bands = ['g', 'r', 'i', 'z', 'y']

	#names to be given to the relevant catalogues
	cat_basic = 'basicclean_catalogue.hdf5'
	cat_main = 'clean_catalogue.hdf5'
	cat_stars = 'star_catalogue.hdf5'
	cat_tomo = 'tomography_catalogue.hdf5'

	#redshift column to use for tomography
	zcol = 'pz_best_dnnz'
	#redshift bin edges
	zbins = [0.3, 0.6, 0.9, 1.2, 1.5]


##################
#### get_data ####
##################

class getData(cf_global):

	#submit/download data requests
	submit = True
	download = True
	#include photo-z information
	photoz = True
	#apply cuts based on existing flags in the catalogues
	strict_cuts = True

	#maximum number of sources allowed before catalogues split for field
	Nmax = 20_000_000

	@classmethod
	def suffix(cls):
		suff = '_forced'
		if not cls.strict_cuts:
			suff += '_shearcat'
		return suff


##########################
#### clean_catalogues ####
##########################

class cleanCats(cf_global):

	#parts of the naming structure for raw data files
	prefix = f'{cf_global.dr.upper()}_'
	suffix = getData.suffix()

	#depth limit in primary band
	depth_cut = 24.5

	#blending cut (maximum allowed flux estimated to be from blending)
	blend_cut = 10. ** (-0.375)

	#S/N thresholds in primary band and other bands
	sn_pri = 10.
	sn_sec = 5.



###################
#### make_maps ####
###################

class makeMaps(cf_global):

	#NSIDE parameter for the low- and high-resolution components of the maps
	nside_lo = 32
	nside_hi = 2048
	#NSIDE for the upgraded-resolution version of the bright object mask
	nside_mask = 16384

	#column names for flags identifying sources near bright objects
	bo_flags = [f'{cf_global.band}_pixelflags_bright_objectcenter',
				f'{cf_global.band}_pixelflags_bright_object']

	#basenames for the various maps
	dustmaps = 'dustmaps.hsp'
	bo_mask = 'bo_mask.hsp'








