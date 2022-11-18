from ..utils import *
from scipy.interpolate import interp1d
import torch.nn as nn
import torch

class Survival():
    """
        Factory object
    """
    @staticmethod
    def create(survival, inputdim, outputdim, survival_args = {}): 
        if survival == 'deepsurv':
            return DeepSurv(inputdim, outputdim, survival_args)
        elif survival == 'deephit':
            return DeepHit(inputdim, outputdim, survival_args)
        elif survival == 'full':
            return Full(inputdim, outputdim, survival_args)
        else:
            raise NotImplementedError()


class DeepSurv(BatchForward):
    """
        DeepSurv
    """

    def __init__(self, inputdim, outputdim, survival_args = {}):
        """
        Args:
            inputdim (int): Input dimension (hidden state)
            outputdim (int): Output dimension (number competing risks)
            survival_args (dict, optional): Arguments for the model. Defaults to {}.
        """
        super(DeepSurv, self).__init__()

        self.inputdim = inputdim
        self.outputdim = outputdim

        survival_layer = survival_args['layers'] if 'layers' in survival_args else [100]
        
        # One neural network for each outcome => More flexibility
        self.survival = nn.ModuleList([nn.Sequential(*create_nn(inputdim, survival_layer + [1])[:-1]) 
                                    for _ in range(outputdim)])

    def forward_batch(self, h):
        outcome = [o(h) for o in self.survival]
        outcome = torch.cat(outcome, -1)
        return outcome,

    def loss(self, h, e, t, batch = None, reduction = 'mean'):
        loss, e = 0, e.squeeze()
        predictions, = self.forward(h, batch = batch)

        ## Sum all previous event : **Require order by decreasing time**
        p_cumsum = torch.logcumsumexp(predictions, 0)
        for ei in range(1, self.outputdim + 1):
            loss -= torch.sum(predictions[e == ei][:, ei - 1])
            loss += torch.sum(p_cumsum[e == ei][:, ei - 1])

        if reduction == 'mean' and (e != 0).sum() > 0:
            loss /= (e != 0).sum()

        return loss

    def compute_baseline(self, h, e, t, batch = None):
        # Breslow estimator
        # At time of the event, the cumulative proba is one
        predictions = torch.exp(self.forward(h, batch = batch)[0])

        # Remove duplicates and order
        self.baselines = []
        self.times, indices = torch.unique(t.squeeze(), return_inverse = True, sorted = True)
        for risk in range(1, self.outputdim + 1):
            e_summed, p_summed = [], []
            for i, _ in enumerate(self.times):
                # Descending order
                e_summed.insert(0, (e[indices == i] == risk).sum())
                p_summed.insert(0, predictions[indices == i][:, risk - 1].sum()) # -1 because only one dimension for each risk (no 0 modelling)

            e_summed, p_summed = torch.DoubleTensor(e_summed), \
                                torch.DoubleTensor(p_summed)
            p_summed = torch.cumsum(p_summed, 0) # Number patients
            
            # Reverse order
            self.baselines.append(torch.cumsum((e_summed / p_summed)[torch.arange(len(self.times), 0, -1) - 1], 0).unsqueeze(0))
        self.baselines = torch.cat(self.baselines, 0)
        return self

    def predict_batch(self, h, horizon, risk = 1):
        forward, = self.forward_batch(h)
        cumulative_hazard = self.baselines[risk - 1].unsqueeze(0)
        if h.is_cuda:
            cumulative_hazard = cumulative_hazard.cuda()

        # exp(W X) * Cum_intensity = Cumulative hazard at time t
        # Survival = exp(-cum hazard) 
        predictions = torch.exp(- torch.matmul(torch.exp(forward[:, risk - 1].unsqueeze(1)), cumulative_hazard))

        if isinstance(horizon, list):
            # Interpolate to make the prediction at the point of interest
            result = []
            for h in horizon:
                _, closest = torch.min((self.times <= h), 0)
                closest -= 1
                if closest < 0:
                    result.append(torch.ones((len(predictions))))
                else:
                    result.append(predictions[:, closest])
            predictions = torch.stack(result).T
        return predictions,
        

class DeepHit(BatchForward):
    def __init__(self):
        pass


class Full(BatchForward):
    def __init__(self):
        pass