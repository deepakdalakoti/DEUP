from matplotlib import pyplot as plt
from argparse import ArgumentParser
import numpy as np
import torch
import torch.nn as nn
from uncertaintylearning.utils import (MAFMOGDensityEstimator, DUQVarianceSource, create_network, create_optimizer, 
                                        create_multiplicative_scheduler, create_wrapped_network, create_epistemic_pred_network)
from uncertaintylearning.utils.resnet import ResNet18plus
from uncertaintylearning.models import DEUPEstimationImage
import torch.optim as optim
import torchvision 
import torchvision.transforms as transforms
from sklearn.metrics import roc_auc_score

parser = ArgumentParser()

parser.add_argument("--save_base_path", default='.',
                    help='name of the function to optimize')

parser.add_argument("--data_base_path", default='data',
                    help='name of the function to optimize')


args = parser.parse_args()

save_base_path = args.save_base_path
data_base_path = args.data_base_path

device=torch.device("cuda" if torch.cuda.is_available() else "cpu")

splits = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9)] 

def get_split_dataset(split_num, dataset):
    idx = torch.logical_or(torch.tensor(dataset.targets)==splits[split_num][0], torch.tensor(dataset.targets)==splits[split_num][1])
    return torch.utils.data.dataset.Subset(dataset, np.where(idx==0)[0])

transform = transforms.Compose([
    transforms.RandomCrop(32, padding=4),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
])

test_transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010))
])

oodset = torchvision.datasets.SVHN(root=data_base_path, split='test',
                                         download=True, transform=test_transform)
oodloader = torch.utils.data.DataLoader(oodset, batch_size=64,
                                         shuffle=False, num_workers=2)

iid_testset = torchvision.datasets.CIFAR10(root=data_base_path, train=False,
                                    download=True, transform=test_transform)
iid_testloader = torch.utils.data.DataLoader(iid_testset, batch_size=128,
                                        shuffle=False, num_workers=2)

dataset = torchvision.datasets.CIFAR10(root=data_base_path, train=True,
                                    download=True, transform=test_transform)
trainloader = torch.utils.data.DataLoader(dataset, batch_size=256,
                                        shuffle=True, num_workers=2)

density_estimator = MAFMOGDensityEstimator(n_components=10, hidden_size=1024, batch_size=100, n_blocks=5, lr=1e-4, use_log_density=True, epochs=32, use_density_scaling=True)
variance_source = DUQVarianceSource(32, 10, 512, 512, 0.1, 0.999, 0.5, device)

networks = {
            'e_predictor': create_network(2, 1, 1024, 'relu', False, 3), # not used in this script
            'f_predictor': ResNet18plus() # use create_wrapped_network("resnet50") for resnet-50
            }

optimizers = {
            'e_optimizer': optim.SGD(networks['e_predictor'].parameters(), lr=0.001, momentum=0.9),
            'f_optimizer': optim.SGD(networks['f_predictor'].parameters(), lr=0.05, momentum=0.9, weight_decay=5e-4)
            }
schedulers = {
    'f_scheduler': torch.optim.lr_scheduler.MultiStepLR(optimizers['e_optimizer'], milestones=[25, 50], gamma=0.2)
}

data = {
    'train_loader': trainloader
}

model = DEUPEstimationImage(data=data,
            networks=networks,
            optimizers=optimizers,
            density_estimator=density_estimator,
            variance_source=variance_source,
            features='bv',
            device=device,
            loss_fn=nn.BCELoss(reduction='none')
        )

model = model.to(device)

model_save_path = save_base_path + "resnet18_cifar_full_new.pt"
epochs = 75
model.fit(epochs=epochs, val_loader=iid_testloader)
torch.save(model.f_predictor, model_save_path)

for split_num in range(len(splits)):

    trainset = get_split_dataset(split_num, dataset)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=256,
                                        shuffle=True, num_workers=2)

    density_estimator = MAFMOGDensityEstimator(n_components=10, hidden_size=1024, batch_size=100, n_blocks=5, lr=9e-5, use_log_density=True, epochs=30, use_density_scaling=True)
    variance_source = DUQVarianceSource(32, 10, 512, 512, 0.1, 0.999, 0.5, device)

    density_save_path = save_base_path + "mafmog_cifar_split_{}_new.pt".format(split_num)
    density_estimator.fit(trainset, device, density_save_path)

    var_save_path = save_base_path + "duq_cifar_split_{}_new.pt".format(split_num)
    variance_source.fit(train_loader=trainloader, save_path=var_save_path)
    variance_source.fit(train_loader=trainloader, save_path=var_save_path, val_loader=iid_testloader)
    
    data = {
        'train_loader': trainloader,
        'ood_loader': oodloader
    }

    model = DEUP(data=data,
                networks=networks,
                optimizers=optimizers,
                density_estimator=density_estimator,
                variance_source=variance_source,
                features='bv',
                device=device,
                use_dataloaders=True,
                loss_fn=nn.BCELoss(reduction='none'),
                batch_size=128
            )

    model_save_path = save_base_path + "resnet18_cifar_split_{}_new.pt".format(split_num)
    model.fit(epochs=epochs, val_loader=iid_testloader)
    torch.save(model.f_predictor, model_save_path)