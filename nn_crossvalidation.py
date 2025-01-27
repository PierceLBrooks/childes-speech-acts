"""Training routine for LSTM and Transformer"""

import argparse
import os
import pickle

import numpy as np

import pandas as pd

import torch
from sklearn.model_selection import train_test_split, KFold
from torch import nn, optim
from torch.utils.data import DataLoader

from nn_dataset import SpeechActsDataset
from nn_models import SpeechActLSTM, SpeechActBERTLSTM, build_vocabulary
from nn_train import prepare_data
from utils import (
    dataset_labels,
    TRAIN_TEST_SPLIT_RANDOM_STATE,
)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

MODEL_TRANSFORMER = "transformer"
MODEL_LSTM = "lstm"

VAL_SPLIT_SIZE = 0.1


def train(args):
    print("Start training with args: ", args)
    print("Device: ", device)

    # Load data
    data = pd.read_pickle(args.data)

    # Split data
    kf = KFold(n_splits=args.num_splits, random_state=TRAIN_TEST_SPLIT_RANDOM_STATE)

    accuracies = []

    file_names = data["file_id"].unique().tolist()
    for i, (train_indices, test_indices) in enumerate(kf.split(file_names)):
        train_files = [file_names[i] for i in train_indices]
        test_files = [file_names[i] for i in test_indices]

        data_train = data[data["file_id"].isin(train_files)]
        data_test = data[data["file_id"].isin(test_files)]

        print(
            f"\n### Training on permutation {i} - {len(data_train)} utterances in train,  {len(data_test)} utterances in test set: "
        )

        print("Building vocabulary..")
        vocab = build_vocabulary(data_train["tokens"], args.vocab_size)
        if not os.path.isdir(args.out):
            os.mkdir(args.out)
        pickle.dump(vocab, open(os.path.join(args.out, "vocab.p"), "wb"))

        label_vocab = dataset_labels()
        pickle.dump(label_vocab, open(os.path.join(args.out, "vocab_labels.p"), "wb"))

        data_train = prepare_data(data_train, vocab, label_vocab)
        data_test = prepare_data(data_test, vocab, label_vocab)

        data_train, data_val = train_test_split(
            data_train,
            test_size=VAL_SPLIT_SIZE,
            shuffle=True,
            random_state=TRAIN_TEST_SPLIT_RANDOM_STATE,
        )

        dataset_train = SpeechActsDataset(data_train)
        dataset_val = SpeechActsDataset(data_val)
        dataset_test = SpeechActsDataset(data_test)

        train_loader = DataLoader(
            dataset_train,
            batch_size=args.batch_size,
            shuffle=True,
            num_workers=0,
        )
        valid_loader = DataLoader(
            dataset_val,
            batch_size=args.batch_size,
            shuffle=True,
            num_workers=0,
        )
        test_loader = DataLoader(
            dataset_test,
            batch_size=args.batch_size,
            shuffle=True,
            num_workers=0,
        )
        print("Loaded data.")

        if args.model == MODEL_LSTM:
            model = SpeechActLSTM(
                len(vocab),
                args.emsize,
                args.nhid_words_lstm,
                args.nhid_utterance_lstm,
                args.nlayers,
                args.dropout,
                len(label_vocab),
            )
        elif args.model == MODEL_TRANSFORMER:
            model = SpeechActBERTLSTM(
                len(label_vocab),
                args.emsize,
                args.nhid_utterance_lstm,
                args.dropout,
                len(label_vocab),
                finetune_bert=True,
            )
        else:
            raise RuntimeError("Unknown model type: ", args.model)

        model.to(device)

        optimizer = optim.Adam(model.parameters(), lr=args.lr)

        def train_epoch(data_loader, epoch):
            model.train()
            total_loss = 0.0

            for batch_id, (input_samples, targets, sequence_lengths, ages) in enumerate(
                data_loader
            ):
                # Move data to GPU
                targets = torch.tensor(targets).to(device)

                # Clear gradients
                optimizer.zero_grad()

                # Perform forward pass of the model
                loss = model(input_samples, targets)

                # Calculate loss
                total_loss += loss.item()
                loss.backward()

                # Clip gradients
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)

                # Update parameter weights
                optimizer.step()

                if batch_id % args.log_interval == 0 and batch_id != 0:
                    cur_loss = total_loss / (args.log_interval * args.batch_size)
                    current_learning_rate = optimizer.param_groups[0]["lr"]
                    print(
                        "| epoch {:3d} | {:5d}/{:5d} batches | lr {:02.6f} | loss {:5.5f}".format(
                            epoch,
                            batch_id,
                            len(data_loader),
                            current_learning_rate,
                            cur_loss,
                        )
                    )
                    total_loss = 0

                if args.dry_run:
                    break

        def evaluate(data_loader):
            # Turn on evaluation mode which disables dropout.
            model.eval()
            total_loss = 0.0
            num_samples = 0
            num_correct = 0
            with torch.no_grad():
                for batch_id, (
                    input_samples,
                    targets,
                    sequence_lengths,
                    ages,
                ) in enumerate(data_loader):
                    # Move data to GPU
                    targets = torch.tensor(targets).to(device)

                    # Perform forward pass of the model
                    predicted_labels = model.forward_decode(input_samples)
                    predicted_labels = torch.tensor(predicted_labels).to(device)

                    # Compare predicted labels to ground truth
                    num_correct += int(torch.sum(predicted_labels == targets))
                    num_samples += len(input_samples)

            return total_loss / num_samples, num_correct / num_samples

        # Loop over epochs.
        best_val_acc = None

        try:
            for epoch in range(1, args.epochs + 1):
                train_epoch(train_loader, epoch)
                val_loss, val_accuracy = evaluate(valid_loader)
                print("-" * 89)
                print(
                    "| end of epoch {:3d} | valid loss {:5.5f} | valid acc {:5.2f} ".format(
                        epoch, val_loss, val_accuracy
                    )
                )
                print("-" * 89)
                # Save the model if the validation loss is the best we've seen so far.
                if not best_val_acc or val_accuracy > best_val_acc:
                    with open(os.path.join(args.out, "model.pt"), "wb") as f:
                        torch.save(model, f)
                    best_val_acc = val_accuracy

        except KeyboardInterrupt:
            print("-" * 89)
            print("Exiting from training early")

        # Load the best saved model.
        with open(f"{args.out}/model.pt", "rb") as f:
            model = torch.load(f)

        # Run on test data.
        test_loss, test_accuracy = evaluate(test_loader)
        print("=" * 89)
        print(
            "| End of training | test loss {:5.2f} | test acc {:5.2f}".format(
                test_loss, test_accuracy
            )
        )
        print("=" * 89)
        accuracies.append(test_accuracy)

    print(
        "| End of crossvalidation | mean acc {:5.4f} | std acc {:5.4f}".format(
            np.mean(accuracies), np.std(accuracies)
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data",
        type=str,
        required=True,
        help="path to the data corpus",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="out/",
        help="directory to store result files",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=MODEL_TRANSFORMER,
        choices=[MODEL_TRANSFORMER, MODEL_LSTM],
        help="model architecture",
    )
    parser.add_argument(
        "--num-splits",
        type=int,
        default=5,
        help="number of splits to perform crossvalidation over",
    )
    parser.add_argument(
        "--vocab-size",
        type=int,
        default=1000,
        help="Maxmimum size of the vocabulary",
    )
    parser.add_argument(
        "--emsize", type=int, default=200, help="size of word embeddings"
    )
    parser.add_argument(
        "--nhid-words-lstm",
        type=int,
        default=200,
        help="number of hidden units of the lower-level LSTM",
    )
    parser.add_argument(
        "--nhid-utterance-lstm",
        type=int,
        default=100,
        help="number of hidden units of the higher-level LSTM",
    )

    parser.add_argument(
        "--nlayers",
        type=int,
        default=1,
        help="number of layers of the lower-level LSTM",
    )
    parser.add_argument(
        "--lr", type=float, default=0.0001, help="initial learning rate"
    )
    parser.add_argument("--clip", type=float, default=0.25, help="gradient clipping")
    parser.add_argument("--epochs", type=int, default=50, help="upper epoch limit")

    # TODO fix: works only with batch size one at the moment (equalling 1 transcript)
    parser.add_argument(
        "--batch-size", type=int, default=1, metavar="N", help="batch size"
    )
    parser.add_argument(
        "--dropout",
        type=float,
        default=0.2,
        help="dropout applied to layers (0 = no dropout)",
    )
    parser.add_argument("--seed", type=int, default=1111, help="random seed")
    parser.add_argument(
        "--log-interval", type=int, default=30, metavar="N", help="report interval"
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="verify the code and the model"
    )

    args = parser.parse_args()
    train(args)
