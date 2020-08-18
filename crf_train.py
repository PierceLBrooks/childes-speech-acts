#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Original code: https://github.com/scrapinghub/python-crfsuite/blob/master/examples/CoNLL%202002.ipynb

Features originally include:
* 'PosTurnSeg<=i', 'PosTurnSeg>=i', 'PosTurnSeg=i' with i in 0, number_segments_turn_position
	* Removed: need adapted parameter, default parameter splits the text into 4 
* 'Length<-]', 'Length]->', 'Length[-]' with i in 0 .. inf with binning
	* Adapted: if need for *more* features, will be added
* 'Spk=='
	* Removed: no clue what this does
* 'Speaker' in ['CHI', 'MOM', etc]
	* kept but simplified

TODO:
* Wait _this_ yields a question: shouldn't we split files if no direct link between sentences? like activities changed
* Split trainings
* ADAPT TAGS: RENAME TAGS

COLUMN NAMES IN FILES:
FILE_ID SPA_X SPEAKER SENTENCE for tsv
SPA_ALL IT TIME SPEAKER SENTENCE for txt - then ACTION

Execute training:
	$ python crf_train.py ttv/childes_ne_train_spa_2.tsv -act  -f tsv
"""
import os
import sys
import random
import codecs
import argparse
import time, datetime
from collections import Counter
import json

import re
import nltk
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import sklearn
from sklearn import svm, naive_bayes, ensemble
from sklearn.metrics import classification_report, confusion_matrix, cohen_kappa_score
from sklearn.preprocessing import LabelBinarizer
import pycrfsuite
from joblib import dump

### Tag functions
from utils import dataset_labels


#### Read Data functions
def argparser():
	"""Creating arparse.ArgumentParser and returning arguments
	"""
	argparser = argparse.ArgumentParser(description='Train a CRF and test it.', formatter_class=argparse.RawTextHelpFormatter)
	# Data files
	argparser.add_argument('train', type=str, help="file listing train dialogs")
	argparser.add_argument('--format', '-f', choices=['txt', 'tsv'], required=True, help="data file format - adapt reading")
	argparser.add_argument('--txt_columns', nargs='+', type=str, default=[], help=""".txt columns name (in order); most basic txt is ['spa_all', 'ut', 'time', 'speaker', 'sentence']""")
	# Operations on data
	argparser.add_argument('--match_age', type=int, nargs='+', default=None, help="ages to match data to - for split analysis")
	argparser.add_argument('--keep_tag', choices=['all', '1', '2', '2a'], default="all", help="keep first part / second part / all tag")
	argparser.add_argument('--cut', type=int, default=1000000, help="if specified, use the first n train dialogs instead of all.")
	argparser.add_argument('--out', type=str, default='results', help="where to write .crfsuite model file")
	# parameters for training:
	argparser.add_argument('--nb_occurrences', '-noc', type=int, default=5, help="number of minimum occurrences for word to appear in features")
	argparser.add_argument('--use_action', '-act', action='store_true', help="whether to use action features to train the algorithm, if they are in the data")
	argparser.add_argument('--use_repetitions', '-rep', action='store_true', help="whether to check in data if words were repeated from previous sentence, to train the algorithm")
	argparser.add_argument('--train_percentage', type=float, default=1., help="percentage (as fraction) of data to use for training. If --conv_split_length is not set, whole conversations will be used.")
	argparser.add_argument('--verbose', action="store_true", help="Whether to display training iterations output.")
	# Baseline model
	argparser.add_argument('--baseline', type=str, choices=['SVC','LSVC', 'NB', 'RF'], default=None, help="which algorithm to use for baseline: SVM (classifier ou linear classifier), NaiveBayes, RandomForest(100 trees)")

	args = argparser.parse_args()
	return args


def openData(list_file:str, cut=100000, column_names=['all', 'ut', 'time', 'speaker', 'sentence'], match_age=None, use_action=False, check_repetition=False):
	"""
	Input:
	------
	list_file: `str`
		location of file containing train/dev/test txt files

	cut: `int`
		number of files to keep
	
	column_names: `list`
		list of features in the text file
	
	match_age: `list`
		list of ages to match column age_months to - if needed by later analysis. Matching column to closest value in list.
	
	use_action: `bool`
		whether to add actions to features
	
	check_repetition: `bool`
		whether to add repetition features

	Output:
	------
	p: `pd.DataFrame`
	"""
	print("Loading ", list_file)
	text_file = open(list_file, "r")
	lines = text_file.readlines() # lines ending with "\n"
	text_file.close()
	# loading data
	p = []
	for i in range(min(len(lines), cut)):
		file_name = lines[i][:-1]
		tmp = pd.read_csv(file_name, sep="\t", names=column_names)
		# either removing empty sentences or replacing with ""
		tmp = tmp[~pd.isna(tmp.sentence)]
		#tmp['sentence'] = tmp.sentence.fillna("")
		tmp['file_id'] = file_name
		tmp['index'] = i
		p.append(tmp)
	p = pd.concat(p)
	# Changing locutors: INV/FAT become mother
	p['speaker'] = p['speaker'].apply(lambda x: x if x in ['CHI', 'MOT'] else 'MOT')
	# Adding features
	p = data_add_features(p, use_action=use_action, match_age=match_age, check_repetition=check_repetition)
	# Splitting tags
	for col_name, t in zip(['spa_1', 'spa_2', 'spa_2a'], ['first', 'second', 'adapt_second']):
		p[col_name] = p['spa_all'].apply(lambda x: select_tag(x, keep_part=t)) # creating columns with different tags
	# Return
	return p

def take_percent(data:pd.DataFrame, fraction:float, conv_column:str, 
					split_avg_lgth:int = 50, split_var_lgth:int=10) -> pd.DataFrame:
	"""Split the data into segments of average length split_avg_lgth, and randomize the extraction of data from the dataset only to keep a given fraction of data. 
	column containing file references is updated to include splits, so that the algorithm doesn't later group data from one file all together despite splits.
	"""
	if (fraction > 1 or fraction < 0):
		raise ValueError("Fraction {fraction} must be between 0. and 1..")
	
	n = data.shape[0]
	split_index = np.cumsum(np.random.normal(split_avg_lgth, split_var_lgth, int(n/split_avg_lgth)).astype(int))
	# update split index with data file changes index
	file_idx = data[data[conv_column] != data[conv_column].shift(1)].index.tolist()
	for f_idx in file_idx:
		split_index[min(range(len(split_index)), key = lambda i: abs(split_index[i]-f_idx))] = f_idx
	
	# split and shuffle
	tmp = []
	for i, idx in enumerate(split_index[:-1]):
		subset = data.iloc[idx:split_index[i+1], :]
		subset[conv_column] = subset[conv_column].apply(lambda x: x+'_'+str(i))
		tmp.append(subset)
	tmp = sklearn.utils.shuffle(tmp)
	tmp = pd.concat(tmp, axis=0)

	# compute fraction and return
	return tmp.iloc[:int(n*fraction), :]

def create_rw_data(data:pd.DataFrame, conv_column:str, split_lgth:int = 50) -> pd.DataFrame:
	"""Split the data into segments of length split_avg_lgth, and use a rolling window to create more training data. 
	column containing file references is updated to include splits, so that the algorithm doesn't later group data from one file all together despite splits.
	"""
	# index of file change in data
	file_idx = data[data[conv_column] != data[conv_column].shift(1)].index.tolist()
	# create rolling windows
	rw_idx = [[(a, min(a+split_lgth, file_idx[i+1])) for a in range(idx, file_idx[i+1], split_lgth)] for i, idx in enumerate(file_idx[:-1])]
	rw_idx = [y for x in rw_idx for y in x] # flatten
	tmp = []
	for i, (idx_start, idx_end) in enumerate(rw_idx):
		subset = data.iloc[idx_start:idx_end, :]
		subset[conv_column] = subset[conv_column].apply(lambda x: x+'_'+str(i))
		tmp.append(subset)
	tmp = sklearn.utils.shuffle(tmp)
	tmp = pd.concat(tmp, axis=0)

	# return data
	return tmp

#### Features functions
def data_add_features(p:pd.DataFrame, use_action=False, match_age=None, check_repetition=False):
	"""Function adding features to the data:
	* tokens: splitting spoken sentence into individual words
	* turn_length
	* tags (if necessary): extract interchange/illocutionary from general tag
	* action_tokens (if necessary): splitting action sentence into individual words
	* age_months: matching age to experimental labels
	* repeted_words:
	* number of repeated words
	* ratio of words that were repeated from previous sentence over sentence length
	"""
	# sentence: using tokens to count & all
	p['tokens'] = p.sentence.apply(lambda x: x.lower().split())
	p['turn_length'] = p.tokens.apply(len)
	# action: creating action tokens
	if use_action:
		p['action'].fillna('', inplace=True)
		p['action_tokens'] = p.action.apply(lambda x: x.lower().split())
	# matching age with theoretical age from the study
	# p['age_months'] = p.file.apply(lambda x: int(x.split('/')[-2])) # NewEngland only
	if 'age_months' in p.columns and match_age is not None:
		match_age = match_age if isinstance(match_age, list) else [match_age]
		p['age_months'] = p.age_months.apply(lambda age: min(match_age, key=lambda x:abs(x-age)))
	# repetition features
	if check_repetition:
		p['prev_file'] = p.file_id.shift(1).fillna(p.file_id.iloc[0])
		p['prev_spk'] = p.speaker.shift(1).fillna(p.speaker.iloc[0])
		p['prev_st'] = p.tokens.shift(1)#.fillna(p.tokens.iloc[0]) # doesn't work - fillna doesn't accept a list as value
		p['prev_st'].iloc[0] = p.tokens.iloc[0]
		p['repeated_words'] = p.apply(lambda x: [w for w in x.tokens if w in x.prev_st] if (x.prev_spk != x.speaker) and (x.file_id == x.prev_file) else [], axis=1)
		p['nb_repwords'] = p.repeated_words.apply(len)
		p['ratio_repwords'] = p.nb_repwords/p.turn_length
		p = p[[col for col in p.columns if col not in ['prev_spk', 'prev_st', 'prev_file']]]
	# return Dataframe
	return p


def word_to_feature(features:dict, spoken_tokens:list, speaker:str, ln:int, action_tokens=None, repetitions=None):
	"""Replacing input list tokens with feature index

	Features should be of type:
	https://python-crfsuite.readthedocs.io/en/latest/pycrfsuite.html#pycrfsuite.ItemSequence
	==> Using Counters

	Input:
	-------
	features: `dict`
		dictionary of all features used, by type: {'words':Counter(), ...}

	spoken_tokens: `list`
		data sentence
	
	speaker: `str`
		MOT/CHI
	
	ln: `int`
		sentence length
	
	action_tokens: `list`
		data action, default None if actions are not taken into account
	
	Output:
	-------
	feat_glob: `dict`
		dictionary of same shape as feature, but only containing features relevant to data line
	"""
	feat_glob = { 'words': Counter([w for w in spoken_tokens if (w in features['words'].keys())]) } # TODO: add 'UNK' token
	feat_glob['speaker'] = {speaker:1.0}
	feat_glob['length'] = {k:(1 if ln <= float(k.split('-')[1]) and ln >= float(k.split('-')[0]) else 0) for k in features['length_bins'].keys()}

	if action_tokens is not None:
		# actions are descriptions just like 'words'
		feat_glob['actions'] = Counter([w for w in action_tokens if (w in features['action'].keys())]) #if (features['action'] is not None) else Counter(action_tokens)
	if repetitions is not None:
		(rep_words, len_rep, ratio_rep) = repetitions
		feat_glob['repeated_words'] = Counter([w for w in rep_words if (w in features['words'].keys())])
		feat_glob['rep_length'] = {k:(1 if len_rep <= float(k.split('-')[1]) and len_rep >= float(k.split('-')[0]) else 0) for k in features['rep_length_bins'].keys()}
		feat_glob['rep_ratio'] = {k:(1 if ratio_rep <= float(k.split('-')[1]) and ratio_rep >= float(k.split('-')[0]) else 0) for k in features['rep_ratio_bins'].keys()}

	return feat_glob


def word_bs_feature(features:dict, spoken_tokens:list, speaker:str, ln:int, action_tokens=None, repetitions=None):
	"""Replacing input list tokens with feature index

	Input:
	-------
	features: `dict`
		dictionary of all features used, by type: {'words':Counter(), ...}

	spoken_tokens: `list`
		data sentence
	
	speaker: `str`
		MOT/CHI
	
	ln: `int`
		sentence length
	
	action_tokens: `list`
		data action, default None if actions are not taken into account
	
	Output:
	-------
	features_glob: `list`
		list of size nb_features, dummy of whether feature is contained or not
	"""
	nb_features = max([max([int(x) for x in v.values()]) for v in features.values()])+1
	# list features
	features_sparse = [features['words'][w] for w in spoken_tokens if w in features['words'].keys()] # words
	features_sparse.append(features['speaker'][speaker]) # locutor
	for k in features['length_bins'].keys(): # length
		if ln <= float(k.split('-')[1]) and ln >= float(k.split('-')[0]):
			features_sparse.append(features['length_bins'][k])

	if action_tokens is not None: # actions are descriptions just like 'words'
		features_sparse += [features['action'][w] for w in spoken_tokens if w in features['action'].keys()]
	if repetitions is not None: # not using words, only ratio+len
		(_, len_rep, ratio_rep) = repetitions
		for k in features['rep_length_bins'].keys():
			if len_rep <= float(k.split('-')[1]) and len_rep >= float(k.split('-')[0]):
				features_sparse.append(features['rep_length_bins'][k])
		for k in features['rep_ratio_bins'].keys():
			if len_rep <= float(k.split('-')[1]) and len_rep >= float(k.split('-')[0]):
				features_sparse.append(features['rep_ratio_bins'][k])
	
	# transforming features
	features_full = [1 if i in features_sparse else 0 for i in range(nb_features)]

	return features_full


### REPORT
def plot_training(trainer, file_name):
	logs = pd.DataFrame(trainer.logparser.iterations) # initially list of dicts
	# columns: {'loss', 'error_norm', 'linesearch_trials', 'active_features', 'num', 'time', 'scores', 'linesearch_step', 'feature_norm'}
	# FYI scores is empty
	logs.set_index('num', inplace=True)
	for col in ['loss', 'active_features']:
		plt.figure()
		plt.plot(logs[col])
		plt.savefig(file_name+'/'+col+'.png')


#### MAIN
if __name__ == '__main__':
	args = argparser()
	print(args)
	if (args.train_percentage is not None) and (args.train_percentage <=0. and args.train_percentage > 1.):
		raise ValueError("--train_percentage must be between 0. and 1. (strictly superior to 0, otherwise no data to train on). Current value: {0}.".format(args.train_percentage))

	print("### Creating features:".upper())

	# Definitions
	number_words_for_feature = args.nb_occurrences # default 5
	number_segments_length_feature = 10
	#number_segments_turn_position = 10 # not used for now
	training_tag = 'spa_'+args.keep_tag

	if args.format == 'txt':
		if args.txt_columns == []:
			raise TypeError('--txt_columns [col0] [col1] ... is required with format txt')
		args.use_action = args.use_action & ('action' in args.txt_columns)
		data_train = openData(args.train, cut=args.cut, column_names=args.txt_columns, match_age=args.match_age, use_action = args.use_action, check_repetition=args.use_repetitions)

	elif args.format == 'tsv':
		data_train = pd.read_csv(args.train, sep='\t').reset_index(drop=False)
		args.use_action = args.use_action & ('action' in data_train.columns.str.lower())
		data_train.rename(columns={col:col.lower() for col in data_train.columns}, inplace=True)
		data_train = data_add_features(data_train, use_action=args.use_action, match_age=args.match_age, check_repetition=args.use_repetitions)
		training_tag = [x for x in data_train.columns if 'spa_' in x][0]
		args.training_tag = training_tag
	
	# printing log data
	print("\nTag counts: ")
	count_tags = data_train[training_tag].value_counts().to_dict()
	for k in sorted(count_tags.keys()):
		print("{}: {}".format(k,count_tags[k]), end=" ; ")

	count_vocabulary = [y for x in data_train.tokens.tolist() for y in x] # flatten
	count_vocabulary = dict(Counter(count_vocabulary))
	# filtering features
	count_vocabulary = {k:v for k,v in count_vocabulary.items() if v > args.nb_occurrences}
	# turning vocabulary into numbered features - ordered vocabulary
	features_idx = {'words': {k:i for i, k in enumerate(sorted(count_vocabulary.keys()))}}
	print("\nThere are {} words in the features".format(len(features_idx['words'])))

	# adding other features:
	count_spk = dict(Counter(data_train['speaker'].tolist()))
	# printing log data:
	print("\nSpeaker counts: ")
	for k in sorted(count_spk.keys()):
		print("{}: {}".format(k,count_spk[k]), end=" ; ")
	#features_idx = {**features_idx, **{k:(len(features_idx)+i) for i, k in enumerate(sorted(count_spk.keys()))}}
	features_idx['speaker'] = {k:(len(features_idx['words'])+i) for i, k in enumerate(sorted(count_spk.keys()))}
	
	data_train['len_bin'], bins = pd.qcut(data_train.turn_length, q=number_segments_length_feature, duplicates='drop', labels=False, retbins=True)
	# printing log data:
	print("\nTurn length splits: ")
	for i,k in enumerate(bins[:-1]):
		print("\tlabel {}: turns of length {}-{}".format(i,k, bins[i+1]))

	nb_feat = max([max(v.values()) for v in features_idx.values()])
	features_idx['length_bins'] = {"{}-{}".format(k, bins[i+1]):(nb_feat+i) for i, k in enumerate(bins[:-1])}
	features_idx['length'] = {i:(nb_feat+i) for i, _ in enumerate(bins[:-1]) }
	# parameters: duplicates: 'raise' raises error if bins are identical, 'drop' just ignores them (leading to the creation of larger bins by fusing those with identical cuts)
	# retbins = return bins (for debug) ; labels=False: only yield the position in the binning, not the name (simpler to create features)

	# features: actions
	if args.use_action:
		count_actions = [y for x in data_train.action_tokens.tolist() for y in x] # flatten
		count_actions = dict(Counter(count_actions))
		# filtering features
		count_actions = {k:v for k,v in count_actions.items() if v > args.nb_occurrences}
		# turning vocabulary into numbered features - ordered vocabulary
		nb_feat = max([max(v.values()) for v in features_idx.values()])
		features_idx['action'] = {k:i+nb_feat for i, k in enumerate(sorted(count_actions.keys()))}
		print("\nThere are {} words in the actions".format(len(features_idx['action'])))	

	if args.use_repetitions:
		nb_feat = max([max(v.values()) for v in features_idx.values()])
		# features esp for length & ratio - repeated words can use previously defined features
		# lengths
		_, bins = pd.qcut(data_train.nb_repwords, q=number_segments_length_feature, duplicates='drop', labels=False, retbins=True)
		features_idx['rep_length_bins'] = {"{}-{}".format(k, bins[i+1]):(nb_feat+i) for i, k in enumerate(bins[:-1])}
		# ratios
		_, bins = pd.qcut(data_train.ratio_repwords, q=number_segments_length_feature, duplicates='drop', labels=False, retbins=True)
		features_idx['rep_ratio_bins'] = {"{}-{}".format(k, bins[i+1]):(nb_feat+i) for i, k in enumerate(bins[:-1])}
		print("\nRepetition ratio splits: ")
		for i,k in enumerate(bins[:-1]):
			print("\tlabel {}: turns of length {}-{}".format(i,k, bins[i+1]))

	# creating crf features set for train
	data_train['features'] = data_train.apply(lambda x: word_to_feature(features_idx, x.tokens, x['speaker'], x.turn_length, None if not args.use_action else x.action_tokens, None if not args.use_repetitions else (x.repeated_words, x.nb_repwords, x.ratio_repwords)), axis=1)

	if args.train_percentage < 1:
		# only take x % of the files
		train_files = data_train['file_id'].unique().tolist()
		train_subset = np.random.choice(len(train_files), size=int(len(train_files)*args.train_percentage), replace=False)
		train_files = [train_files[x] for x in train_subset]
		data_train = data_train[data_train['file_id'].isin(train_files)]

	# Once the features are done, groupby name and extract a list of lists
	# some "None" appear bc some illocutionary codes missing - however creating separations between data...
	grouped_train = data_train.dropna(subset=[training_tag]).groupby(by=['file_id']).agg({
		'features' : lambda x: [y for y in x],
		training_tag : lambda x: [y for y in x], 
		'index': min
	}) # listed by apparition order
	grouped_train = sklearn.utils.shuffle(grouped_train)

	# After that, train ---
	print("\n### Training starts.".upper())
	trainer = pycrfsuite.Trainer(verbose=args.verbose)
	# Adding data
	for idx, file_data in grouped_train.iterrows():
		trainer.append(file_data['features'], file_data[training_tag]) # X_train, y_train
	# Parameters
	trainer.set_params({
			'c1': 1,   # coefficient for L1 penalty
			'c2': 1e-3,  # coefficient for L2 penalty
			'max_iterations': 50,  # stop earlier
			'feature.possible_transitions': True # include transitions that are possible, but not observed
	})

	# Location for weight save
	name = os.path.join(os.getcwd(),('' if args.out is None else args.out), 
				'_'.join([ x for x in [training_tag, datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')] if x ])) # creating name with arguments, removing Nones in list
	print("Saving model at: {}".format(name))
	os.mkdir(name)
	trainer.train(os.path.join(name, 'model.pycrfsuite'))
	# plotting training curves
	plot_training(trainer, name)
	# dumping features
	with open(os.path.join(name, 'features.json'), 'w') as json_file:
		json.dump(features_idx, json_file)
	# dumping metadata
	with open(os.path.join(name, 'metadata.txt'), 'w') as meta_file:
		for arg in vars(args):
			meta_file.write("{0}:\t{1}\n".format(arg, getattr(args, arg)))
	
	# Baseline
	if args.baseline is not None:
		print("\nTraining and saving baseline model for comparison.")
		X = data_train.dropna(subset=[training_tag]).apply(lambda x: word_bs_feature(features_idx, x.tokens, x['speaker'], x.turn_length, None if not args.use_action else x.action_tokens, None if not args.use_repetitions else (x.repeated_words, x.nb_repwords, x.ratio_repwords)), axis=1)
		y = data_train.dropna(subset=[training_tag])[training_tag].tolist()
		# ID from label - bidict
		labels = dataset_labels(training_tag.upper())
		# transforming
		X = np.array(X.tolist())
		y = np.array([labels[lab] for lab in y]) # to ID
		# TODO: take imbalance into account
		models = {
			'SVC': svm.SVC(),
			'LSVC': svm.LinearSVC(), 
			'NB': naive_bayes.GaussianNB(), 
			'RF': ensemble.RandomForestClassifier(n_estimators=100)
		}

		models[args.baseline].fit(X,y)
		dump(models[args.baseline], os.path.join(name, 'baseline.joblib'))