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


from tfrddlsim.policy import Policy
from rddl2tf.compiler import Compiler

import numpy as np
import tensorflow as tf

from typing import Callable, Optional, Sequence


class DeepReactivePolicy(Policy):

    def __init__(self,
            compiler: Compiler,
            layers: Sequence[int],
            activation: Callable[[tf.Tensor], tf.Tensor],
            input_layer_norm: Optional[bool] = False,
            hidden_layer_norm: Optional[bool] = False) -> None:
        self._compiler = compiler
        self._saver = None
        self.layers = layers
        self.activation_fn = activation
        self.input_layer_norm = input_layer_norm
        self.hidden_layer_norm = hidden_layer_norm

    @property
    def graph(self):
        return self._compiler.graph

    @property
    def name(self):
        return 'drp-fc-layers={}'.format('+'.join(map(str, self.layers)))

    @property
    def size(self):
        with self.graph.as_default():
            policy_vars = tf.trainable_variables(r'.*\/policy')
            params = sum(np.prod(v.shape.as_list()) for v in policy_vars)
            return params

    @property
    def vars(self):
        with self.graph.as_default():
            policy_vars = tf.trainable_variables(r'.*\/policy')
            return policy_vars

    def save(self, sess, save_path=None):
        if self._saver is None:
            self._saver = tf.train.Saver()
        if save_path is None:
            save_path = '/tmp/{}/model.ckpt'.format(self.name)
        self._checkpoint = self._saver.save(sess, save_path)
        return self._checkpoint

    def restore(self, sess, save_path=None):
        if self._saver is None:
            self._saver = tf.train.Saver()
        if save_path is None:
            save_path = self._checkpoint
        self._saver.restore(sess, save_path)

    def __call__(self,
            state: Sequence[tf.Tensor],
            timestep: tf.Tensor) -> Sequence[tf.Tensor]:
        with self.graph.as_default():
            with tf.variable_scope('policy', reuse=tf.AUTO_REUSE):
                self._state_inputs(state)
                self._input_layer()
                self._hidden_layers()
                self._output_layer()
                self._action_outputs(state)
            action = self.action_outputs
            return action

    def _state_inputs(self, state):
        self._batch_size = int(state[0].shape[0])
        reshape = lambda fluent: tf.reshape(fluent, [self._batch_size, -1])
        self.state_inputs = tuple(map(reshape, state))

    def _input_layer(self):
        with tf.variable_scope('input'):
            layers = []
            state_fluents = self._compiler.state_fluent_ordering
            for fluent_name, fluent_input in zip(state_fluents, self.state_inputs):
                layer = fluent_input
                if self.input_layer_norm:
                    fluent_name = fluent_name.replace('/', '-')
                    with tf.variable_scope(fluent_name):
                        layer = tf.contrib.layers.layer_norm(fluent_input)
                layers.append(layer)
            self.input_layer = tf.concat(layers, axis=1)

    def _hidden_layers(self):
        self.hidden = [self.input_layer]
        layer = self.input_layer
        for l, units in enumerate(self.layers):
            with tf.variable_scope('hidden{}'.format(l+1)):
                if self.hidden_layer_norm:
                    activation = tf.layers.dense(layer, units)
                    layer = tf.contrib.layers.layer_norm(activation, activation_fn=self.activation_fn)
                else:
                    layer = tf.layers.dense(layer, units, activation=self.activation_fn)
                self.hidden.append(layer)
        self.hidden = tuple(self.hidden)

    def _output_layer(self):
        action_fluents = self._compiler.action_fluent_ordering
        action_size = self._compiler.action_size
        inputs = self.hidden[-1]
        self.output_layer = []
        for fluent_name, fluent_size in zip(action_fluents, action_size):
            fluent_name = fluent_name.replace('/', '-')
            units = np.prod(fluent_size)
            with tf.variable_scope('output/{}'.format(fluent_name)):
                layer = tf.layers.dense(inputs, units)
                self.output_layer.append(layer)
        self.output_layer = tuple(self.output_layer)

    def _action_outputs(self, state):
        bounds = self._compiler.compile_action_bound_constraints(state)
        action_fluents = self._compiler.action_fluent_ordering
        action_size = self._compiler.action_size
        layers = self.output_layer
        self.action_outputs = []
        for fluent_name, fluent_size, layer in zip(action_fluents, action_size, layers):
            scope = fluent_name.replace('/', '-')
            with tf.name_scope(scope):
                action_tensor = tf.reshape(layer, [self._batch_size] + list(fluent_size))
                action_tensor = self._get_output_tensor(action_tensor, bounds[fluent_name])
                self.action_outputs.append(action_tensor)
        self.action_outputs = tuple(self.action_outputs)

    def _get_output_tensor(self, tensor, bounds):
        lower, upper = bounds
        if lower is not None:
            lower = lower.cast(tf.float32)
            lower = tf.stop_gradient(lower.tensor)
        if upper is not None:
            upper = upper.cast(tf.float32)
            upper = tf.stop_gradient(upper.tensor)

        if lower is not None and upper is not None:
            tensor = lower + (upper - lower) * tf.sigmoid(tensor)
        elif lower is not None and upper is None:
            tensor = lower + tf.exp(tensor)
        elif lower is None and upper is not None:
            tensor = upper - tf.exp(tensor)

        return tensor
