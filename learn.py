#!/usr/bin/env python3


from __future__ import division
import argparse
import os
import sys
DIR = os.path.dirname(os.path.realpath(__file__)) 
# os.chdir(DIR)
sys.path.insert(0, DIR)
import numpy as np
import gym
import gym_micropolis
import gtk
from gym.utils import seeding

from keras.models import Sequential, load_model
from keras.layers import Dense, Activation, Flatten, Convolution2D, ConvLSTM2D, Permute, Reshape

from keras.optimizers import Adam
from keras.callbacks import Callback, TensorBoard, ModelCheckpoint
from time import time
import keras.backend as K

from rl.agents.dqn import DQNAgent
from rl.policy import LinearAnnealedPolicy, BoltzmannQPolicy, EpsGreedyQPolicy
from rl.memory import SequentialMemory
from rl.core import Processor
from rl.callbacks import FileLogger, ModelIntervalCheckpoint

from time import strftime

print("Started at",strftime("%Y-%m-%d-%H:%M:%S"))

RUNDIR = DIR + '/runs'

parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['train', 'test'], default='train')
parser.add_argument('--env-name', type=str, default='MicropolisEnv-v0')
parser.add_argument('--weights', type=str, default=None)
#parser.add_argument('--loadmodel', type=str, default=None)
parser.add_argument('--run-id', type=str, default=None)

args = parser.parse_args()
print('ARG LIST: {}'.format(args))
# Get the environment and extract the number of actions.
env = gym.make(args.env_name)
MAP_X, MAP_Y = 15, 15
PADDING = 0
env.SHOW_GUI = True

env.setMapSize(MAP_X, MAP_Y, PADDING)
np.random.seed(420)
env.seed(420)
nb_actions = env.action_space.n
num_zones = env.num_zones
num_tools = env.num_tools


INPUT_SHAPE = (MAP_X + PADDING, MAP_Y + PADDING)
WINDOW_LENGTH = 1


class MicropolisProcessor(Processor):
    def process_observation(self, observation):
        assert observation.ndim == 3  # (height, width, channel)
     #  img = Image.fromarray(observation)
     #  img = img.resize(INPUT_SHAPE).convert('L')  # resize and convert to grayscale
     #  processed_observation = np.array(img)
     #  assert processed_observation.shape == INPUT_SHAPE
        return observation.astype('bool')  # saves storage in experience memory

    def process_state_batch(self, batch):
        # We could perform this processing step in `process_observation`. In this case, however,
        # we would need to store a `float32` array instead, which is 4x more memory intensive than
        # an `uint8` array. This matters if we store 1M observations.
        processed_batch = batch.astype('bool')
        return processed_batch

    def process_reward(self, reward):
     #  return np.clip(reward, -1., 1.)
        return reward

PAD_X = MAP_X + 2 * PADDING
PAD_Y = MAP_Y + 2 * PADDING

# Next, we build our model. We use the same model that was described by Mnih et al. (2015).
input_shape = (num_zones, PAD_X, PAD_Y)
model = Sequential()
model.add(Reshape((input_shape), input_shape=(WINDOW_LENGTH,) + input_shape))
if K.image_dim_ordering() == 'tf':
    print('tensorflow ordering')
    # (width, height, channels)
    model.add(Permute((2, 3, 1), input_shape=input_shape))
    permute_shape = (PAD_X, PAD_Y, num_zones)
elif K.image_dim_ordering() == 'th':
    # (channels, width, height)
    model.add(Permute((0, 1, 2), input_shape=input_shape))
    permute_shape = (num_zones, PAD_X, PAD_Y)
else:
    raise RuntimeError('Unknown image_dim_ordering.')

model.add(Convolution2D(32, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
model.add(Convolution2D(64, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
model.add(Convolution2D(64, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
model.add(Convolution2D(32, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
model.add(Convolution2D(32, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
model.add(Convolution2D(16, (3, 3), strides=(1, 1), padding='same'))
model.add(Activation('relu'))
#model.add(Reshape((1,) + (PAD_X, PAD_Y, 32)))
#model.add(ConvLSTM2D(16, (3, 3), strides=(1, 1), padding='same'))
#model.add(Activation('tanh'))
model.add(Convolution2D(num_tools, (3, 3), strides=(1, 1), padding='same'))
model.add(Flatten())
model.add(Activation('linear'))
print(model.summary())

# Finally, we configure and compile our agent. You can use every built-in Keras optimizer and
# even the metrics!
memory = SequentialMemory(limit=1000000, window_length=WINDOW_LENGTH)
processor = MicropolisProcessor()

# Select a policy. We use eps-greedy action selection, which means that a random action is selected
# with probability eps. We anneal eps from 1.0 to 0.1 over the course of 1M steps. This is done so that
# the agent initially explores the environment (high eps) and then gradually sticks to what it knows
# (low eps). We also set a dedicated eps value that is used during testing. Note that we set it to 0.05
# so that the agent still performs some random actions. This ensures that the agent cannot get stuck.
policy = LinearAnnealedPolicy(EpsGreedyQPolicy(), attr='eps', value_max=1, value_min=.1, value_test=.05,
                              nb_steps=1000000)

# The trade-off between exploration and exploitation is difficult and an on-going research topic.
# If you want, you can experiment with the parameters or use a different policy. Another popular one
# is Boltzmann-style exploration:
# policy = BoltzmannQPolicy(tau=1.)
# Feel free to give it a try!

dqn = DQNAgent(model=model, nb_actions=nb_actions, policy=policy, memory=memory,
               processor=processor, nb_steps_warmup=50000, gamma=.99, target_model_update=10000,
               train_interval=4, delta_clip=1.)
dqn.compile(Adam(lr=.00025), metrics=['mae'])

if args.mode == 'train':
    # Okay, now it's time to learn something! We capture the interrupt exception so that training
    # can be prematurely aborted. Notice that you can use the built-in Keras callbacks!
    os.chdir(DIR)
    os.chdir('runs')
    if not args.run_id:
        run_id = '{}_{}'.format(args.env_name, time())
    else: 
        run_id = args.run_id
    os.mkdir(run_id)
    weights_filename = '{}/weights.h5f'.format(run_id)
    checkpoint_weights_filename = weights_filename + '_{step}.h5f'
 #  model_filename = 'dqn_micropolis_model.hdf5'
    log_filename = '{}/log.json'.format(run_id)
    callbacks = [ModelIntervalCheckpoint(checkpoint_weights_filename, interval=250000)]
  # callbacks += [ModelCheckpoint(model_filename)]
    callbacks += [FileLogger(log_filename, interval=250000)]
    callbacks += [TensorBoard(log_dir=run_id)]
#   class TestCallback(Callback):
#       def on_epoch_end(self, epoch, logs=None):
#           test_env = gym.make(args.env_name)
#           test_env.setMapSize(MAP_X,MAP_Y)
#           dqn.test(test_env, nb_episodes=1, visualize=True, nb_max_start_steps=100)
#           test_env.win1.destroy()
#           test_env.close()
#           del(test_env)
#   callbacks += [TestCallback()]
#   if args.loadmodel:
#       dqn.model.load(args.loadmodel)
    args.weights = None
    print("args.weights--",args.weights)

    if args.weights:
        dqn.load_weights(args.weights)

    dqn.fit(env, callbacks=callbacks, nb_steps=100000, log_interval=10000)

    # After training is done, we save the final weights one more time.
    dqn.save_weights(weights_filename, overwrite=True)
  # dqn.save_model(model_filename)

    # Finally, evaluate our algorithm for 10 episodes.
    dqn.test(env, nb_episodes=10, visualize=True)
   #dqn.test(env, nb_episodes=10, visualize=True)
 #  gtk.main()

elif args.mode == 'test':
    weights_filename = 'dqn_{}_weights.h5f'.format(args.env_name)
    if args.weights:
        weights_filename = args.weights
    dqn.load_weights(weights_filename)
    dqn.test(env, nb_episodes=10, visualize=True, nb_max_start_steps=00)
