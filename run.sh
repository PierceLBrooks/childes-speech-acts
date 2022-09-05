python3 -m pip install -r ./requirements.txt
python3 ./preprocess.py --corpora NewEngland --drop-untagged
python3 ./crf_train.py --use-pos --use-bi-grams --use-repetitions
python3 ./crf_test.py -m ./checkpoints/crf/ --use-pos --use-bi-grams --use-repetitions
python3 ./preprocess.py --corpora Rollins --drop-untagged -o ~/data/speech_acts/data/rollins_preprocessed.p
python3 ./crf_test.py --data ~/data/speech_acts/data/rollins_preprocessed.p -m ./checkpoints/crf/ --use-pos --use-bi-grams --use-repetitions
python3 ./crf_annotate.py --model ./checkpoint_full_train --data ./examples/example.csv --out ./data_annotated/example.csv --use-pos --use-bi-grams --use-repetitions

