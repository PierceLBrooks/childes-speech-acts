import argparse
import pickle

import pandas as pd
import torch
from torch.utils.data import DataLoader

from torch import cuda

from dataset import SpeechActsDataset, pad_batch
from models import SpeechActDistilBERT

device = "cuda" if cuda.is_available() else "cpu"


def calcuate_accu(predicted_labels, targets):
    n_correct = (predicted_labels == targets).sum().item()
    return n_correct


def train(args):
    vocab = pickle.load(open(args.data + "vocab.p", "rb"))
    label_vocab = pickle.load(open(args.data + "vocab_labels.p", "rb"))

    # TODO use BERT tokenizer?
    train_dataframe = pd.read_hdf(args.data + "speech_acts_data.h5", "train")
    val_dataframe = pd.read_hdf(args.data + "speech_acts_data.h5", "val")
    test_dataframe = pd.read_hdf(args.data + "speech_acts_data.h5", "test")

    dataset_train = SpeechActsDataset(train_dataframe)
    dataset_val = SpeechActsDataset(val_dataframe)
    dataset_test = SpeechActsDataset(test_dataframe)

    train_loader = DataLoader(
        dataset_train,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
        collate_fn=pad_batch,
    )
    valid_loader = DataLoader(
        dataset_val,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
        collate_fn=pad_batch,
    )
    test_loader = DataLoader(
        dataset_test,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
        collate_fn=pad_batch,
    )

    model = SpeechActDistilBERT(num_classes=len(label_vocab), dropout=args.dropout)
    model.to(device)

    loss_function = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(params=model.parameters(), lr=args.lr)

    def train_epoch(epoch):
        tr_loss = 0
        n_correct = 0
        nb_tr_steps = 0
        nb_tr_examples = 0
        model.train()
        for i, (input_samples, targets, sequence_lengths) in enumerate(train_loader):
            input_samples = input_samples.to(device)
            targets = targets.to(device)
            sequence_lengths = sequence_lengths.to(device)

            outputs = model(input_samples, sequence_lengths)

            loss = loss_function(outputs, targets)
            tr_loss += loss.item()
            _, predicted_labels = torch.max(outputs.data, dim=1)
            n_correct += calcuate_accu(predicted_labels, targets)

            nb_tr_steps += 1
            nb_tr_examples += targets.size(0)

            if i % 5000 == 0:
                loss_step = tr_loss / nb_tr_steps
                accu_step = (n_correct * 100) / nb_tr_examples
                print(f"Training Loss per 5000 steps: {loss_step}")
                print(f"Training Accuracy per 5000 steps: {accu_step}")

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        print(
            f"The Total Accuracy for Epoch {epoch}: {(n_correct * 100) / nb_tr_examples}"
        )
        epoch_loss = tr_loss / nb_tr_steps
        epoch_accu = (n_correct * 100) / nb_tr_examples
        print(f"Training Loss Epoch: {epoch_loss}")
        print(f"Training Accuracy Epoch: {epoch_accu}")

        return

    for epoch in range(args.epochs):
        train_epoch(epoch)

        # Saving the files for re-use
        # TODO: only if best model so far
        torch.save(model, args.save)
        print("Model checkpoint saved")

    def evaluate(model, loader):
        model.eval()
        n_correct = 0
        tr_loss = 0
        nb_tr_steps = 0
        nb_tr_examples = 0
        with torch.no_grad():
            for i, (input_samples, targets, sequence_lengths) in enumerate(loader):
                input_samples = input_samples.to(device)
                targets = targets.to(device)
                sequence_lengths = sequence_lengths.to(device)

                outputs = model(input_samples, sequence_lengths)

                loss = loss_function(outputs, targets)
                tr_loss += loss.item()
                _, predicted_labels = torch.max(outputs.data, dim=1)
                n_correct += calcuate_accu(predicted_labels, targets)

                nb_tr_steps += 1
                nb_tr_examples += targets.size(0)

                if i % 5000 == 0:
                    loss_step = tr_loss / nb_tr_steps
                    accu_step = (n_correct * 100) / nb_tr_examples
                    print(f"Validation Loss per 100 steps: {loss_step}")
                    print(f"Validation Accuracy per 100 steps: {accu_step}")
        epoch_loss = tr_loss / nb_tr_steps
        epoch_accu = (n_correct * 100) / nb_tr_examples
        print(f"Validation Loss Epoch: {epoch_loss}")
        print(f"Validation Accuracy Epoch: {epoch_accu}")

        return epoch_accu

    print("\nEvaluation")

    acc = evaluate(model, test_loader)
    print("Accuracy on test data = %0.2f%%" % acc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data",
        type=str,
        default="./data/",
        help="location of the data corpus and vocabs",
    )
    parser.add_argument("--lr", type=float, default=1e-05, help="initial learning rate")
    parser.add_argument("--epochs", type=int, default=20, help="upper epoch limit")
    parser.add_argument(
        "--batch-size", type=int, default=50, metavar="N", help="batch size"
    )
    parser.add_argument(
        "--dropout",
        type=float,
        default=0.3,
        help="dropout applied to layers (0 = no dropout)",
    )
    parser.add_argument("--seed", type=int, default=1111, help="random seed")
    parser.add_argument(
        "--save", type=str, default="model.pt", help="path to save the final model"
    )

    args = parser.parse_args()
    train(args)
