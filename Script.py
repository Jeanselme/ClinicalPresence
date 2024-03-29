#!/usr/bin/env python
from tqdm import tqdm
import pandas as pd
import numpy as np

import argparse
parser = argparse.ArgumentParser(description = 'Running split.')
parser.add_argument('--mode', '-m', type = int, default = 0, help = 'Mode for training (1, -1) : (weekend, weekday); (2, -2): (male, female); (3, -3): (teaching, non teaching); 0 : Random.', choices = range(-3,4))
parser.add_argument('--dataset', '-d',  type = str, default = 'mimic', help = 'Dataset to use: mimic, eicu, ')
parser.add_argument('--sub', '-s', action='store_true', help = 'Run on subset of vitals.')
parser.add_argument('--over', '-o', action='store_true', help = 'Oversample smaller set.')
args = parser.parse_args()



# This number is used only for training, the testing happens only on the first 24 hours to ensure that
# each patient has the same impact on the final performance computation
labs = pd.read_csv('data/{}/labs_first_day_subselection.csv'.format(args.dataset), index_col = [0, 1]) if args.sub else pd.read_csv('data/{}/labs_first_day.csv'.format(args.dataset), index_col = [0, 1], header = [0, 1])
outcomes = pd.read_csv('data/{}/outcomes_first_day{}.csv'.format(args.dataset, '_subselection' if args.sub else ''), index_col = 0)

if args.dataset == 'mimic':
    outcomes['Death'] = ~outcomes.Death.isna()
    assert abs(args.mode) < 3, 'Mode not adapted for the selected dataset.'

# # Split 
ratio = 0. 
results = 'results_subselection/' if args.sub else 'results/' 
results += args.dataset + '/'
if args.mode == 0:
    print("Applied on Random")
    training = pd.Series(outcomes.index.isin(outcomes.sample(frac = 0.8, random_state = 0).index), index = outcomes.index)
    results += 'random/'
elif args.mode == 1:
    print("Applied on Weekdays")
    training = outcomes.Day <= 4
    results += 'weekdays/'
elif args.mode == -1:
    print("Applied on Weekends")
    training = outcomes.Day > 4
    results += 'weekends/'
    ratio = (1-training).sum() / training.sum() if args.over else 0
elif args.mode == 2:
    print("Applied on Private")
    training = outcomes.INSURANCE == 'Private'
    results += 'insured/'
    ratio = (1-training).sum() / training.sum() if args.over else 0
elif args.mode == -2:
    print("Applied on Public")
    training = outcomes.INSURANCE != 'Private'
    results += 'uninsured/'
elif args.mode == -3:
    print("Applied on Teaching hospitals")
    training = outcomes.teachingstatus == 't'
    results += 'teaching/'
    ratio = (1-training).sum() / training.sum() if args.over else 0
elif args.mode == 3:
    print("Applied on Non Teaching hospitals")
    training = outcomes.teachingstatus == 'f'
    results += 'nonteaching/'

results += 'survival_'

print('Total patients: {}'.format(len(training)))
print('Training patients: {}'.format(training.sum()))

from experiment import ShiftExperiment
def process(data, labels):
    """
        Extracts mask and interevents
        Preprocesses the time of event and event
    """
    cov = data.copy().astype(float)
    cov = cov.groupby('Patient').ffill() 
    
    patient_mean = data.astype(float).groupby('Patient').mean()
    cov.fillna(patient_mean, inplace=True) 

    pop_mean = patient_mean.mean()
    cov.fillna(pop_mean, inplace=True) 

    ie_time = data.groupby("Patient").apply(lambda x: x.index.get_level_values('Time').to_series().diff().fillna(0))
    mask = ~data.isna() 
    time_event = pd.DataFrame((labels.LOS.loc[data.index.get_level_values(0)] - data.index.get_level_values(1)).values, index = data.index)

    return cov, ie_time, mask, time_event, labels.Death


layers = [[], [50], [50, 50], [50, 50, 50]]

# LOCF
last = labs.groupby('Patient').ffill().groupby('Patient').last().fillna(labs.groupby('Patient').mean().mean()) 

se = ShiftExperiment.create(model = 'deepsurv', 
                    hyper_grid = {"survival_args": [{"layers": l} for l in layers],
                        "lr" : [1e-3, 1e-4],
                        "batch": [100, 250]
                    }, 
                    path = results + 'deepsurv_last')


se.train(last, outcomes.Remaining, outcomes.Death, training, oversampling_ratio = ratio)

# Count
count = (~labs.isna()).groupby('Patient').sum() # Compute counts

se = ShiftExperiment.create(model = 'deepsurv', 
                    hyper_grid = {"survival_args": [{"layers": l} for l in layers],
                        "lr" : [1e-3, 1e-4],
                        "batch": [100, 250]
                    }, 
                    path = results + 'deepsurv_count')


se.train(pd.concat([last, count], axis = 1), outcomes.Remaining, outcomes.Death, training, oversampling_ratio = ratio)

hyper_grid = {
        "layers": [1, 2, 3],
        "hidden": [10, 30],
        "survival_args": [{"layers": l} for l in layers],

        "lr" : [1e-3, 1e-4],
        "batch": [100, 250]
    }

# LSTM with value
cov, ie, mask, time, event = process(labs.copy(), outcomes)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid,
                    path = results + 'lstm_value')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# LSTM with input
labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], axis = 1)
labs_selection['Time'] = labs_selection.index.to_frame().reset_index(drop = True).groupby('Patient').diff().fillna(0).values
cov, ie, mask, time, event = process(labs_selection, outcomes)


se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid,
                    path = results + 'lstm_value+time+mask')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Resampling
labs_resample = labs.copy()
labs_resample = labs_resample.set_index(pd.to_datetime(labs_resample.index.get_level_values('Time'), unit = 'D'), append = True) 
labs_resample = labs_resample.groupby('Patient').resample('1H', level = 2).mean() 
labs_resample.index = labs_resample.index.map(lambda x: (x[0], x[1].hour / 24))

cov, ie, mask, time, event = process(labs_resample, outcomes) 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid,
                    path = results + 'lstm+resampled')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# GRUD
hyper_grid_gru = hyper_grid.copy()
hyper_grid_gru["typ"] = ['GRUD']

labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], axis = 1)
cov, ie, mask, time, event = process(labs_selection, outcomes)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_gru,
                    path = results + 'gru_d+mask')

se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "temporal": ["point"], 
        "temporal_args": [{"layers": l} for l in layers],
        "longitudinal": ["neural"], 
        "longitudinal_args": [{"layers": l} for l in layers],
        "missing": ["neural"], 
        "missing_args": [{"layers": l} for l in layers],
    }
)

# Joint full
labs_selection = labs.copy()
cov, ie, mask, time, event = process(labs_selection, outcomes)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Joint GRU-D
hyper_grid_joint_gru = hyper_grid_joint.copy()
hyper_grid_joint_gru["typ"] = ['GRUD']

labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], axis = 1)
cov, ie, mask, time, event = process(labs_selection, outcomes)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint_gru,
                    path = results + 'joint_gru_d+mask')

se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Joint with full input
labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], 1)
labs_selection['Time'] = labs_selection.index.to_frame().reset_index(drop = True).groupby('Patient').diff().fillna(0).values
cov, ie, mask, time, event = process(labs_selection, outcomes)

mask_mixture = np.full(len(cov.columns), False)
mask_mixture[:len(labs.columns)] = True

hyper_grid_joint['mixture_mask'] = [mask_mixture] 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint_value+time+mask')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Joint GRU-D with full input
labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], 1)
labs_selection['Time'] = labs_selection.index.to_frame().reset_index(drop = True).groupby('Patient').diff().fillna(0).values
cov, ie, mask, time, event = process(labs_selection, outcomes)

mask_mixture = np.full(len(cov.columns), False)
mask_mixture[:len(labs.columns)] = True

hyper_grid_joint_gru['mixture_mask'] = [mask_mixture] 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint_gru,
                    path = results + 'joint_gru_d_value+time+mask')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Full Fine Tune
hyper_grid_joint['full_finetune'] = [True] 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint_full_finetune_value+time+mask')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)


# ##################
# # ABLATION STUDY #
# ##################

# Measure impact of modelling the outcome with same input
labs_selection = labs.copy()
cov, ie, mask, time, event = process(labs_selection, outcomes)

hyper_grid_joint = hyper_grid.copy() #L
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "longitudinal": ["neural"], 
        "longitudinal_args": [{"layers": l} for l in layers],
    }
)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-long')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)


hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "temporal": ["point"], 
        "temporal_args": [{"layers": l} for l in layers],
    }
)
se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-time')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)


hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "missing": ["neural"], 
        "missing_args": [{"layers": l} for l in layers],
    }
)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-missing')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "longitudinal": ["neural"], 
        "longitudinal_args": [{"layers": l} for l in layers],
        "temporal": ["point"], 
        "temporal_args": [{"layers": l} for l in layers],
    }
)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-long-time')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "longitudinal": ["neural"], 
        "longitudinal_args": [{"layers": l} for l in layers],
        "missing": ["neural"], 
        "missing_args": [{"layers": l} for l in layers],
    }
)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-long-missing')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "temporal": ["point"], 
        "temporal_args": [{"layers": l} for l in layers],
        "missing": ["neural"], 
        "missing_args": [{"layers": l} for l in layers],
    }
)
# Joint temporal output only
labs_selection = labs.copy()
cov, ie, mask, time, event = process(labs_selection, outcomes)

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint+value-time-missing')


se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)


# Impact of input
hyper_grid_joint = hyper_grid.copy()
hyper_grid_joint.update(
    {
        "weight": [0.1, 0.3, 0.5],
        "temporal": ["point"], 
        "temporal_args": [{"layers": l} for l in layers],
        "longitudinal": ["neural"], 
        "longitudinal_args": [{"layers": l} for l in layers],
        "missing": ["neural"], 
        "missing_args": [{"layers": l} for l in layers],
    }
)
# Joint with value + time only
labs_selection = labs.copy()
labs_selection['Time'] = labs_selection.index.to_frame().reset_index(drop = True).groupby('Patient').diff().fillna(0).values
cov, ie, mask, time, event = process(labs_selection, outcomes)

mask_mixture = np.full(len(cov.columns), False)
mask_mixture[:len(labs.columns)] = True

hyper_grid_joint['mixture_mask'] = [mask_mixture] 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint_value+time')

se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

# Joint with value + mask only
labs_selection = pd.concat([labs.copy(), labs.isna().add_suffix('_mask').astype(float)], 1)
cov, ie, mask, time, event = process(labs_selection, outcomes)

mask_mixture = np.full(len(cov.columns), False)
mask_mixture[:len(labs.columns)] = True

hyper_grid_joint['mixture_mask'] = [mask_mixture] 

se = ShiftExperiment.create(model = 'joint', 
                    hyper_grid = hyper_grid_joint,
                    path = results + 'joint_value+mask')

se.train(cov, time, event, training, ie, mask, oversampling_ratio = ratio)

