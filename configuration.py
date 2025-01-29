#####################################################################################################
# Configuration class for the various stages of the GC-HSC3 pipeline.
#####################################################################################################

import yaml

class DictAsMember(dict):
	'''
	Class for converting dictionary entries into object
	attributes. Allows for nested dictionaries.
	'''
	def __getattr__(self, name):
		value = self[name]
		if isinstance(value, dict):
			value = DictAsMember(value)
		return value
	

class PipelineConfig():
	''' 
	A class for identifying and defining the settings for the pipeline,
	both globally and at the specified stage.
	'''

	def __init__(self, config_file, stage=None):
		'''
		Loads the contents of a YAML file and uses it to define
		properties representing settings for the pipeline.
		'''
		
		with open(config_file) as f:
			config_all = yaml.safe_load(f)
			self.config_dict = config_all['global']
			if stage is not None:
				self.config_dict = {**self.config_dict, **config_all[stage]}
		
		#define a new property containing all bands
		self.config_dict['bands']['all'] = [self.bands.primary] + self.bands.secondary

		#set the map and catalogue names
		self._set_output_names()


	def __getattr__(self, name):
		'''
		Enables retrieval of pipeline settings as object attributes
		rather than dictionary entries.
		'''
		value = self.config_dict[name]
		if isinstance(value, dict):
			value = DictAsMember(value)
		return value


	def _set_output_names(self):
		'''
		Appends the base names for each map file with the specified suffix
		and (where appropriate) the NSIDE used for the analysis.
		'''
		#hdf5 catalogues
		for key in self.cats:
			self.config_dict['cats'][key] = self.cats[key] + self.suffix + '.hdf5'
		
		#healsparse maps
		for key in self.maps:
			#if dustmaps, replace name with list of names for all bands
			if key == 'dustmaps':
				self.config_dict['maps'][key] = [self.maps[key] 
									 			+ f'_{b}_nside{self.nside_hi}{self.suffix}.hsp'
												for b in self.bands.all]
			else:
				self.config_dict['maps'][key] = self.maps[key] + f'_nside{self.nside_hi}{self.suffix}.hsp'
		
		#n(z) hdf5 files
		for key in self.nofz_files:
			self.config_dict['nofz_files'][key] = self.nofz_files[key] + self.suffix + '.hdf5'
		
		#power spectra hdf5 files
		for key in self.cell_files:
			self.config_dict['cell_files'][key] = self.cell_files[key] + f'_nside{self.nside_hi}{self.suffix}.hdf5'
		
		#cache files
		for key in self.cache_files.workspaces:
			self.config_dict['cache_files']['workspaces'][key] = self.cache_files.workspaces[key] + \
																f'_nside{self.nside_hi}{self.suffix}.fits'
		for key in self.cache_files.deproj:
			self.config_dict['cache_files']['deproj'][key] = self.cache_files.deproj[key] + \
																f'_nside{self.nside_hi}{self.suffix}.txt'
		for key in self.cache_files.hods:
			self.config_dict['cache_files']['hods'][key] = self.cache_files.hods[key] + \
																f'_nside{self.nside_hi}{self.suffix}.txt'



	def get_subfields(self):
		'''
		Identifies which subfields belong to the fields specified in the config file.
		'''
		subfields = []
		if 'hectomap' in self.fields:
			subfields.append('hectomap')
		if 'spring' in self.fields:
			subfields.extend([f'equator{i:02d}' for i in [21,22,23,0,1,2]])
		if 'autumn' in self.fields:
			subfields.extend([f'equator{i:02d}' for i in [8,9,10,11,12,13,14,15]])
		if 'cosmos' in self.fields:
			subfields.append('cosmos')

		return subfields


	def get_samples(self, cat):
		'''
		Returns masks to apply to the input catalogue in order to select sources
		belonging to each sample.

		Parameters
		----------
		cat: h5py._hl.files.File or h5py._hl.group.Group
			The input catalogue or Group. Must contain the relevant Datasets
			needed to define the cuts.
		
		Returns
		-------
		sample_masks: list[numpy.array]
			List of boolean arrays identifying which sources in the catalogue
			belong to each sample.
		'''
		import numpy as np

		sample_masks = []
		for s in self.samples:
			samp = self.samples[s]
			#replace any references to key columns with their actual names
			for key in self.key_cols:
				samp = samp.replace(key, f'cat["{self.key_cols[key]}"][:]')
			#split the expression at any semicolons and evaluate each term
			samp = samp.split(';')
			
			samp_mask = np.ones_like(cat[next(iter(cat.keys()))][:], dtype=bool)
			for ex in samp:
				samp_mask *= eval(ex)
			sample_masks.append(samp_mask)
		return sample_masks
	

	@staticmethod
	def get_field_boundaries(field):
		'''
		Given the name of an HSC field, will return the approximate corner coordinates of
		a rectangular boundary encompassing the field. These coordinates are listed as
		[RA_min, RA_max, Dec_min, Dec_max].

		Parameters
		----------
		field: str
			Name of the field whose boundaries are to be returned. Must be either 'hectomap',
			'spring', 'autumn', or 'aegis'.
		
		Returns
		-------
		bounds: list[float]
			Coordinates defining the boundary of the field, given as [RA_min, RA_max, Dec_min,
			Dec_max]. In the event that the field crosses RA=0, RA_min will lie westward of 
			this longitude, and RA_max will lie eastward (in this situation, RA_min > RA_max).
		'''
		if field == 'hectomap':
			bounds = [195., 255., 41.5, 45.]
		elif field == 'spring':
			bounds = [326.25, 41.25, -8., 8.]
		elif field == 'autumn':
			bounds = [125., 227.5, -4., 7.]
		elif field == 'aegis':
			bounds = [212., 216., 51.6, 53.6]
		else:
			raise ValueError('field must be either "hectomap", "spring", "autumn", or "aegis".')
		return bounds