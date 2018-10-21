# This file is part of tf-mdp.

# tf-mdp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# tf-mdp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with tf-mdp. If not, see <http://www.gnu.org/licenses/>.


from tfmdp.train.policy import DeepReactivePolicy
from tfmdp.train.optimizer import PolicyOptimizer

import tensorflow as tf


class PolicyOptimizationPlanner(object):

    _loss_fn ={
        'linear': lambda r: tf.losses.absolute_difference(0, r),
        'mse': lambda r: tf.losses.mean_squared_error(0, r),
        'huber': lambda r: tf.losses.huber_loss(0, r)
    }

    _non_linearities = {
        'none': None,
        'sigmoid': tf.sigmoid,
        'tanh': tf.tanh,
        'relu': tf.nn.relu,
        'relu6': tf.nn.relu6,
        'crelu': tf.nn.crelu,
        'elu': tf.nn.elu,
        'selu': tf.nn.selu,
        'softplus': tf.nn.softplus,
        'softsign': tf.nn.softsign
    }

    _optimizers = {
        'Adadelta': tf.train.AdadeltaOptimizer,
        'Adagrad': tf.train.AdagradOptimizer,
        'Adam': tf.train.AdamOptimizer,
        'GradientDescent': tf.train.GradientDescentOptimizer,
        'ProximalGradientDescent': tf.train.ProximalGradientDescentOptimizer,
        'ProximalAdagrad': tf.train.ProximalAdagradOptimizer,
        'RMSProp': tf.train.RMSPropOptimizer
    }

    def __init__(self,
            compiler,
            layers, activation, input_layer_norm, hidden_layer_norm,
            logdir=None):
        self._compiler = compiler
        self._policy = DeepReactivePolicy(self._compiler, layers, self._non_linearities[activation], input_layer_norm, hidden_layer_norm)
        self._logdir = logdir

    def build(self, learning_rate, batch_size, horizon, optimizer='RMSProp', loss='linear'):
        self._optimizer = PolicyOptimizer(self._compiler, self._policy, self._logdir)
        self._optimizer.build(
            learning_rate, batch_size, horizon,
            self._optimizers[optimizer], self._loss_fn[loss])

    def run(self, epochs, show_progress=True):
        losses, rewards = self._optimizer.run(epochs, show_progress=show_progress)
        logdir = self._optimizer._train_writer.get_logdir()
        return rewards, self._policy, logdir
