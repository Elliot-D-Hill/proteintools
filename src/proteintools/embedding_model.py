import time

from sklearn.model_selection import train_test_split
from torch import int64, tensor, cat, no_grad, save
from torch import nn
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader
from torchtext.data.functional import to_map_style_dataset


class SequenceClassifier(nn.Module):
    def __init__(self, vocabulary_size, embedding_size, n_classes):
        super(SequenceClassifier, self).__init__()
        self.embedding = nn.EmbeddingBag(vocabulary_size, embedding_size, sparse=False)
        self.fc = nn.Linear(embedding_size, n_classes)
        self.init_weights()

    def init_weights(self):
        initrange = 0.5
        self.embedding.weight.data.uniform_(-initrange, initrange)
        self.fc.weight.data.uniform_(-initrange, initrange)
        self.fc.bias.data.zero_()

    def forward(self, text, offsets):
        embedded = self.embedding(text, offsets)
        return self.fc(embedded)


class EmbeddingModel:
    def __init__(
        self,
        label_map,
        vocabulary,
        label_type,
        training_parameters,
        model,
        criterion,
        optimizer,
        scheduler,
        device,
    ):
        self.device = device
        # dataset
        self.label_map = label_map
        self.vocabulary = vocabulary
        self.label_type = label_type
        # training hyperparameters
        self.batch_size = training_parameters.batch_size
        self.n_epochs = training_parameters.n_epochs
        self.clip_grad = training_parameters.clip_grad
        # model objects
        self.model = model
        self.criterion = criterion
        self.optimizer = optimizer
        self.scheduler = scheduler

    def get_label(self, _label):
        return self.label_map[_label]

    def collate_batch(self, batch):
        label_list, text_list, offsets = [], [], [0]
        for (_label, _text) in batch:
            label_list.append(self.get_label(_label))
            processed_text = tensor(get_tokens(_text, self.vocabulary), dtype=int64)
            text_list.append(processed_text)
            offsets.append(processed_text.size(0))
        label_list = tensor(label_list, dtype=self.label_type)  # .float().unsqueeze(1)
        offsets = tensor(offsets[:-1]).cumsum(dim=0)
        text_list = cat(text_list)
        return (
            label_list.to(self.device),
            text_list.to(self.device),
            offsets.to(self.device),
        )

    def make_dataloader(self, X, y):
        data_iterator = make_dataset_iterator(X, y)
        dataset_map = to_map_style_dataset(data_iterator)
        return DataLoader(
            dataset_map,
            batch_size=self.batch_size,
            shuffle=True,
            collate_fn=self.collate_batch,
        )

    def train_epoch(self, dataloader, epoch, log_interval=10):
        total_accuracy = 0
        total_count = 0
        n_batches = len(dataloader)
        for idx, (labels, text, offsets) in enumerate(dataloader):
            self.optimizer.zero_grad()
            predicted_label = self.model(text, offsets)
            loss = self.criterion(input=predicted_label, target=labels)
            loss.backward()
            clip_grad_norm_(self.model.parameters(), self.clip_grad)
            self.optimizer.step()
            total_accuracy, total_count = calculate_metric(
                predicted_label, labels, total_accuracy, total_count
            )
            if idx % log_interval == 0 and idx > 0:
                accuracy = total_accuracy / total_count
                print_batch_info(epoch, idx, n_batches, accuracy)
                total_accuracy, total_count = 0, 0

    def evaluate(self, dataloader):
        self.model.eval()
        total_accuracy = 0
        total_count = 0
        with no_grad():
            for label, text, offsets in dataloader:
                predicted_label = self.model(text, offsets)
                total_accuracy, total_count = calculate_metric(
                    predicted_label, label, total_accuracy, total_count
                )
        return total_accuracy / total_count

    def train_model(self, train_loader, validation_loader=None):
        self.model.train()
        total_accuracy = None
        for epoch in range(1, self.n_epochs + 1):
            epoch_start_time = time.time()
            self.train_epoch(train_loader, epoch)
            if validation_loader is not None:
                accuracy_val = self.evaluate(validation_loader)
                if total_accuracy is not None and total_accuracy > accuracy_val:
                    self.scheduler.step()
                else:
                    total_accuracy = accuracy_val
                print_epoch_info(epoch, epoch_start_time, accuracy_val)

    def calculate_test_accuracy(self, X_train, y_train, X_test, y_test, X_val, y_val):
        train_loader = self.make_dataloader(X_train, y_train)
        validation_loader = self.make_dataloader(X_val, y_val)
        test_loader = self.make_dataloader(X_test, y_test)
        self.train_model(train_loader, validation_loader)
        test_accuracy = self.evaluate(test_loader)
        return test_accuracy

    def fit(self, X, y):
        train_loader = self.make_dataloader(X, y)
        self.train_model(train_loader)

    def predict(self, text):
        self.model.eval()
        with no_grad():
            text = tensor(get_tokens(text, self.vocabulary))
            output = self.model(text, tensor([0]))
            return output.argmax(1).item()


def calculate_metric(predicted_label, label, total_accuracy, total_count):
    total_accuracy += (predicted_label.argmax(1) == label).sum().item()
    total_count += label.size(0)
    return total_accuracy, total_count


def build_vocabulary(text_list):
    unique_tokens = set("".join(text_list))
    return {value: key for key, value in enumerate(unique_tokens)}


def print_batch_info(epoch, idx, n_batches, metric):
    text = f"| epoch {epoch} | {idx} / {n_batches} batches | metric: {metric:.5f} |"
    print(text)


def log_accuracy(
    total_acc, total_count, predicted_label, label, idx, log_interval, epoch, n_batches
):
    total_acc += (predicted_label.argmax(1) == label).sum().item()
    total_count += label.size(0)
    if idx % log_interval == 0 and idx > 0:
        print_batch_info(epoch, idx, n_batches, total_acc, total_count)
        total_acc, total_count = 0, 0
    return total_acc, total_count


def print_epoch_info(epoch, start_time, metrics):
    print("-" * 85)
    time_i = time.time() - start_time
    text = f"| end of epoch {epoch:3d} | time: {time_i:5.2f}s | validation metrics: {metrics} "
    print(text)
    print("-" * 85)


# TODO check that X and y are in correct order
def make_dataset_iterator(X, y):
    return iter([(y_i, X_i) for y_i, X_i in zip(y, X)])


def get_tokens(text, vocabulary):
    return [vocabulary.get(key) for key in list(text)]
