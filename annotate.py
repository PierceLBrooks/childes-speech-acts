import argparse
import pickle
from collections import Counter

import torch
import pandas as pd
from scipy.stats import entropy
from torch.utils.data import DataLoader
import seaborn as sns

import matplotlib.pyplot as plt
from tqdm import tqdm

from dataset import SpeechActsDataset, SpeechActsTestDataset
from generate_dataset import PADDING, SPEAKER_ADULT, SPEAKER_CHILD, UNKNOWN, preprend_speaker_token

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")



def get_words(indices, vocab):
    return " ".join([vocab.itos[i] for i in indices if not vocab.itos[i] == PADDING])

def annotate(args):
    print("Start annotation with args: ", args)
    print("Device: ", device)
    # Load data
    vocab = pickle.load(open(args.data + "vocab.p", "rb"))
    label_vocab = pickle.load(open(args.data + "vocab_labels.p", "rb"))

    print("Loading data..")
    data = pickle.load(open(args.data + args.corpus, "rb"))
    data = pd.DataFrame(data)

    # Replace speaker column values
    data["speaker"] = data["speaker"].apply(
        lambda x: "CHI" if x == "Target_Child" else "MOT"
    )

    data["tokens"] = data.apply(lambda row: preprend_speaker_token(row["tokens"], row["speaker"]), axis=1)
    data["utterances"] = data.tokens.apply(lambda tokens: [vocab.stoi[t] for t in tokens])

    data = data.groupby(by=["file_id"]).agg({"utterances": lambda x: [y for y in x]})

    dataset_test = SpeechActsTestDataset(data)

    test_loader = DataLoader(
        dataset_test,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
    )

    print("Test samples: ", len(dataset_test))

    def evaluate(data_loader):
        # Turn on evaluation mode which disables dropout.
        model.eval()
        all_predicted_labels = []
        speaker_is_child = []
        with torch.no_grad():
            for batch_id, (input_samples, sequence_lengths) in tqdm(enumerate(data_loader), total=len(data_loader)):

                # Perform forward pass of the model
                predicted_labels = model.forward_decode(input_samples)
                predicted_labels = torch.tensor(predicted_labels).to(device)

                speaker_is_child += [True if x[0] == vocab.stoi[SPEAKER_CHILD] else False for x in input_samples]
                all_predicted_labels += predicted_labels.tolist()

                if args.verbose:
                    for i, (sample, predicted) in enumerate(zip(input_samples, predicted_labels)):
                        print(
                            f"{get_words(sample, vocab)} Predicted: {label_vocab.inverse[int(predicted)]}"
                        )


        predicted_labels_child = [label_vocab.inverse[label] for label, is_child in zip(all_predicted_labels, speaker_is_child) if is_child]

        print("=" * 89)

        counts = Counter(predicted_labels_child)
        for k in counts.keys():
            if counts[k]:
                counts[k] /= len(predicted_labels_child)
            else:
                counts[k] = 0

        gold_frequencies = pickle.load(open(args.compare, "rb"))
        counts = {k: counts[k] for k in gold_frequencies.keys()}

        kl_divergence = entropy(
            list(counts.values()), qk=list(gold_frequencies.values())
        )
        print(f"KL Divergence: {kl_divergence:.3f}")

        labels = list(gold_frequencies.keys()) * 2
        source = ["Gold"] * len(gold_frequencies) + ["Predicted"] * len(gold_frequencies)
        counts = list(gold_frequencies.values()) + list(counts.values())
        df = pd.DataFrame(zip(labels, source, counts), columns=["speech_act", "source", "frequency"])
        plt.figure(figsize=(10, 6))
        sns.barplot(x="speech_act", hue="source", y="frequency", data=df)
        plt.title(f"{args.data} compared to {args.compare} | KL Divergence: {kl_divergence:.3f}")
        plt.show()

    # Load the saved model checkpoint.
    with open(args.checkpoint, "rb") as f:
        model = torch.load(f, map_location=device)

    # Run on test data.
    print("Eval:")
    evaluate(test_loader)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data",
        type=str,
        default="data/",
        help="location of the data corpus and vocabs",
    )
    parser.add_argument(
        "--corpus",
        type=str,
        default="speech_acts_data_newengland.h5",
        help="name of the corpus file",
    )
    parser.add_argument(
        "--compare", type=str, required=True, help="Path to frequencies to compare to"
    )
    # TODO fix: works only with batch size one at the moment
    parser.add_argument(
        "--batch-size", type=int, default=1, metavar="N", help="batch size"
    )
    parser.add_argument("--seed", type=int, default=1111, help="random seed")
    parser.add_argument(
        "--checkpoint",
        type=str,
        default="model.pt",
        help="path to saved model checkpoint",
    )
    parser.add_argument('--verbose', '-v', action="store_true",
                           help="Increase verbosity")

    args = parser.parse_args()
    annotate(args)
