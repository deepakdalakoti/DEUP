import torch
import torch.nn as nn
import numpy as np
from botorch.models.model import Model
from botorch.posteriors.gpytorch import GPyTorchPosterior
from gpytorch.distributions import MultivariateNormal

from torch.utils.data import DataLoader, TensorDataset
from uncertaintylearning.utils import get_ensemble_uncertainty_estimate


class Ensemble(Model):
    def __init__(self, train_X, train_Y,
                 networks,
                 optimizers,
                 schedulers=None,
                 batch_size=16,
                 device=torch.device("cpu")):  # For now, the code runs on CPU only, `.to(self.device)` should be added!
        super(Ensemble, self).__init__()
        self.train_X = train_X
        self.train_Y = train_Y

        self.is_fitted = False

        self.input_dim = train_X.size(1)
        self.output_dim = train_Y.size(1)

        self.epoch = 0
        self.loss_fn = nn.MSELoss()

        self.f_predictors = networks
        self.device = device
        self.f_optimizers = optimizers

        self.actual_batch_size = min(batch_size, len(self.train_X) // 2)

        self.schedulers = schedulers
        if schedulers is None:
            self.schedulers = {}

    @property
    def num_outputs(self):
        return self.output_dim

    def fit(self):
        """
        Update a,f,e predictors with acquired batch
        """

        self.train()
        data = TensorDataset(self.train_X, self.train_Y)

        loader = DataLoader(data, shuffle=True, batch_size=self.actual_batch_size)
        for (predictor, optimizer) in zip(self.f_predictors, self.f_optimizers):
            for batch_id, (xi, yi) in enumerate(loader):
                xi, yi = xi.to(self.device), yi.to(self.device)
                optimizer.zero_grad()
                y_hat = predictor(xi)
                f_loss = self.loss_fn(y_hat, yi)
                f_loss.backward()
            optimizer.step()

        self.epoch += 1
        for scheduler in self.schedulers.values():
            scheduler.step()

        self.is_fitted = True
        return {
            'f': f_loss.detach().item(),
        }

    def get_prediction_with_uncertainty(self, x):
        if not self.is_fitted:
            raise Exception('Model not fitted')
        if x.ndim == 3:
            preds = self.get_prediction_with_uncertainty(x.view(x.size(0) * x.size(1), x.size(2)))
            return preds[0].view(x.size(0), x.size(1), 1), preds[1].view(x.size(0), x.size(1), 1)
        return get_ensemble_uncertainty_estimate(self.f_predictors, x)

    def posterior(self, x):
        # this works with 1d output only
        # x should be a n x d tensor
        mvn = self.forward(x)
        return GPyTorchPosterior(mvn)

    def forward(self, x):
        if x.ndim == 3:
            assert x.size(1) == 1
            return self.forward(x.squeeze(1))
        means, var = self.get_prediction_with_uncertainty(x)
        mvn = MultivariateNormal(means, var.unsqueeze(-1))
        return mvn
        
        # ONLY WORKS WITH 1d output !!!!!
        # When x is of shape n x d, the posterior should have mean of shape n, and covar of shape n x n (diagonal)
        # When x is of shape n x q x d, the posterior should have mean of shape n x 1, and covar of shape n x q x q ( n diagonals)
        means, variances = self.get_prediction_with_uncertainty(x)

        # Sometimes the predicted variances are too low, and MultivariateNormal doesn't accept their range
        # TODO: maybe the two cases can be merged into one with torch.diag_embed
        if means.ndim == 2:
            mvn = MultivariateNormal(means.squeeze(), torch.diag(variances.squeeze() + 1e-6))
        elif means.ndim == 3:
            assert means.size(-1) == variances.size(-1) == 1
            try:
                mvn = MultivariateNormal(means.squeeze(-1), torch.diag_embed(variances.squeeze(-1)) + 1e-6)
            except RuntimeError:
                print('RuntimeError')
                print(torch.diag_embed(variances.squeeze(-1)) + 1e-6)
        else:
            raise NotImplementedError("Something is wrong, just cmd+f this error message and you can start debugging.")
        return mvn