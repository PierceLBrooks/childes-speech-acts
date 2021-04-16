# Speech Act Annotations
Repository for classification of speech acts in child-caregiver conversations using CRFs, LSTMs and Transformers.

As recommended by the [CHAT transcription format](https://talkbank.org/manuals/CHAT.pdf), we use INCA-A as speech acts
annotation scheme.

# Environment
An anaconda environment can be setup by using the `environment.yml` file:
```
conda env create -f environment.yml
conda activate speech-acts
```

# Preprocessing data for supervised training of classifiers

Data for supervised training is taken from the [New England corpus](https://childes.talkbank.org/access/Eng-NA/NewEngland.html) of [CHILDES](https://childes.talkbank.org/access/) and then converted to XML:

1. Download the [New England Corpus data](https://childes.talkbank.org/data/Eng-NA/NewEngland.zip).
2. Convert the data using the [chatter java app](https://talkbank.org/software/chatter.html):
    ```
    $ java -cp chatter.jar org.talkbank.chatter.App [location_of_downloaded_corpus] -inputFormat cha -outputFormat xml -tree -outputDir java_out 
    ```
3. Preprocess data
    ```
    python preprocess.py --input-path java_out/ --output-path data/new_england_preprocessed.p --drop-untagged
   ```
  
# CRF  
## Train CRF classifier

```
python crf_train.py data/new_england_preprocessed.p [optional_feature_args]
```

## Test CRF classifier

Test the classifier on the same corpus:
```
python crf_test.py data/new_england_preprocessed.p -m checkpoints/crf/
```

Test the classifier on the [Rollins corpus](https://childes.talkbank.org/access/Eng-NA/Rollins.html):
1. Use the steps described above to download the corpus and preprocess it.
2. Test the classifier on the corpus.
   ```
   python crf_test.py data/rollins_preprocessed.p -m checkpoints/crf/
   ```
# Neural Networks
## Train LSTM classifier
```
python nn_train.py --data data/new_england_preprocessed.p --model lstm --epochs 50
```

## Test LSTM classifier
```
python nn_test.py --model out --data data/new_england_preprocessed.p
```

