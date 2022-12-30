from ..utils import *
import torch.nn as nn
import torch

class Missing():
    """
        Factory object
    """
    @staticmethod
    def create(missing, inputdim, outputdim, missing_args = {}): 
        if missing == 'None':
            return None
        elif missing == 'neural':
            return Neural(inputdim, outputdim, missing_args)
        else:
            raise NotImplementedError()


class Neural(BatchForward):
    """
        Neural with BCE error
    """

    def __init__(self, inputdim, outputdim, missing_args = {}):
        """
        Args:
            inputdim (int): Input dimension (hidden state)
            outputdim (int): Output dimension (original input dimension)
            missing_args (dict, optional): Arguments for the model. Defaults to {}.
        """
        super(Neural, self).__init__()

        self.inputdim = inputdim
        self.outputdim = outputdim

        missing_layer = missing_args['layers'] if 'layers' in missing_args else [100]
        self.missing = nn.Sequential(*create_nn(inputdim + 1, missing_layer + [outputdim])[:-1], nn.Sigmoid()) # Time might be informative : + 1

    def forward_batch(self, h, i, m, l):
        # Predict next step observation (shorten time)
        concat = torch.cat((h[:, :-1, :], i[:, :-1, :].abs().min(dim = 2)[0].unsqueeze(-1)), 2)
        missing = self.missing(concat)
        return missing,

    def loss(self, alpha, h, i, m, l, batch = None, reduction = 'mean'):
        predictions, = self.forward(h, i, m, l, batch = batch)
        # Compare what is predicted for the next step to what is observed next
        loss = (alpha.flatten() * nn.BCELoss(reduction = "none")(predictions.flatten(), m[:, 1:, :].flatten().double())).sum()

        if reduction == 'mean':
            loss /= m[:, 1:, :].sum()

        return loss