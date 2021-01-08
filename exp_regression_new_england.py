import argparse
import pickle

from sklearn.feature_selection import f_regression
from sklearn.linear_model import LinearRegression
from sklearn.metrics import explained_variance_score

from age_of_acquisition import TARGET_PRODUCTION, TARGET_COMPREHENSION, MAX_AGE
from crf_annotate import calculate_frequencies
import matplotlib.pyplot as plt

import numpy as np

import pandas as pd

import seaborn as sns

from preprocess import SPEECH_ACT

# TODO: set to reasonable value
MAX_AGE_OF_ACQUISITION = MAX_AGE

if __name__ == "__main__":
    argparser = argparse.ArgumentParser()
    argparser.add_argument(
        "--target",
        type=str,
        default=TARGET_PRODUCTION,
        choices=[TARGET_PRODUCTION, TARGET_COMPREHENSION],
    )
    argparser.add_argument(
        "--scores", type=str, default="checkpoints/crf/classification_scores_adult.p"
    )

    args = argparser.parse_args()

    print("Loading data...")
    ages_of_acquisition = pickle.load(
        open(f"results/age_of_acquisition_{args.target}.p", "rb")
    )

    scores = pickle.load(open(args.scores, "rb"))
    scores_f1 = scores["f1-score"].to_dict()

    # Calculate overall adult speech act frequencies
    data = pd.read_pickle("data/new_england_preprocessed.p")
    data_adults = data[data["speaker"] != "CHI"]
    data_children = data[data["speaker"] == "CHI"]

    frequencies_adults = calculate_frequencies(data_adults[SPEECH_ACT])
    frequencies_children = calculate_frequencies(data_children[SPEECH_ACT])
    frequencies = calculate_frequencies(data[SPEECH_ACT])

    observed_speech_acts = list(ages_of_acquisition.keys())

    # Filter out speech acts that do not have an F1 score
    observed_speech_acts = [s for s in observed_speech_acts if s in scores_f1]

    # Filter out speech acts were acquisition data is insufficient
    observed_speech_acts = [
        s
        for s in observed_speech_acts
        if ages_of_acquisition[s] < MAX_AGE_OF_ACQUISITION
    ]

    # Filter data for observed speech acts
    ages_of_acquisition = [ages_of_acquisition[s] for s in observed_speech_acts]
    frequencies_adults = [frequencies_adults[s] for s in observed_speech_acts]
    scores_f1 = [scores_f1[s] for s in observed_speech_acts]

    # Convert frequencies to log scale
    frequencies_adults = np.log10(np.array(frequencies_adults))

    features = frequencies_adults.reshape(-1, 1)
    targets = ages_of_acquisition
    reg = LinearRegression().fit(features, targets)
    y_pred = reg.predict(features)
    print("Explained variance (only freq):", explained_variance_score(targets, y_pred))
    print("Regression parameters: ", reg.coef_)

    features = np.array(scores_f1).reshape(-1, 1)
    targets = ages_of_acquisition
    reg = LinearRegression().fit(features, targets)
    y_pred = reg.predict(features)
    print(
        "Explained variance (only f1 scores):",
        explained_variance_score(targets, y_pred),
    )
    print("Regression parameters: ", reg.coef_)

    features = np.array(list(zip(frequencies_adults, scores_f1)))
    reg = LinearRegression().fit(features, targets)
    y_pred = reg.predict(features)
    print(
        "Explained variance (freq + f1 scores):",
        explained_variance_score(targets, y_pred),
    )
    print("Regression parameters: ", reg.coef_)
    F, p_val = f_regression(features, targets)
    print("p-values: ", p_val)

    fig, ax = plt.subplots()
    x = ages_of_acquisition
    y = list(scores_f1)
    g = sns.regplot(x, y, ci=None, order=1)
    plt.xlabel(f"{args.target}: age of acquisition (months)")
    plt.ylabel("classification score (F1)")
    plt.title(f"p-value: {p_val[1]:.3f}")

    for i, speech_act in enumerate(observed_speech_acts):
        ax.annotate(speech_act, (x[i], y[i]))

    fig, ax = plt.subplots()
    x = ages_of_acquisition
    y = list(frequencies_adults)
    g = sns.regplot(x, y, ci=None, order=1)
    plt.xlabel(f"{args.target}: age of acquisition (months)")
    plt.ylabel("log frequency (%)")
    plt.title(f"p-value: {p_val[0]:.3f}")

    for i, speech_act in enumerate(observed_speech_acts):
        ax.annotate(speech_act, (x[i], y[i]))

    plt.show()
