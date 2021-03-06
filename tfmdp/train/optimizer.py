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

from rddl2tf.compiler import Compiler

import re
import sys
import numpy as np
import tensorflow as tf

from typing import Callable, List, Optional, Sequence


class PolicyOptimizer(object):

    def __init__(self,
            model,
            logdir: Optional[str] = None,
            hooks=None,
            debug=False) -> None:
        self._model = model
        self._logdir = logdir if logdir is not None else '/tmp'
        self._hooks = hooks
        self._debug = debug

    @property
    def graph(self) -> tf.Graph:
        '''Returns the model's graph.'''
        return self._model.graph

    def build(self,
            learning_rate: float,
            batch_size: int,
            horizon: int,
            optimizer: tf.train.Optimizer,
            kernel_regularizer: Optional[Callable[[tf.Tensor], tf.Tensor]] = None,
            bias_regularizer: Optional[Callable[[tf.Tensor], tf.Tensor]] = None) -> None:
        with self.graph.as_default():
            with tf.name_scope('policy_optimizer'):
                self._build_reward_graph()
                self._build_loss_graph()
                self._build_regularization_loss_graph(kernel_regularizer, bias_regularizer)
                self._build_optimization_graph(optimizer, learning_rate)
                self._build_summary_graph()
                self._build_debug_graph()

    def run(self, epochs: int, show_progress: bool = True, baseline_flag=False) -> None:

        with tf.Session(graph=self.graph) as sess:

            self._train_writer = tf.summary.FileWriter(self._logdir + '/train', sess.graph)
            self._test_writer = tf.summary.FileWriter(self._logdir + '/test', sess.graph)

            self._init_op = tf.global_variables_initializer()
            self._merged = tf.summary.merge_all()

            reward = -sys.maxsize
            losses = []
            rewards = []

            self._hooks_setup()

            sess.run(self._init_op)

            feed_dict = {}
            if baseline_flag:
                baseline = self._model._baseline_fn
                feed_dict = {
                    baseline._training: False
                }

            for step in range(epochs):

                if baseline_flag and step % 5 == 0:
                    print('Fitting baseline function ...')
                    baseline.fit(sess, 64, 50, show_progress=True)
                    print()

                _, loss_, reward_ = sess.run([self._train_op, self.loss, self.avg_total_reward], feed_dict=feed_dict)

                summary_ = sess.run(self._merged, feed_dict=feed_dict)
                self._train_writer.add_summary(summary_, step)

                if reward_ > reward:
                    reward = reward_
                    rewards.append((step, reward_))
                    losses.append((step, loss_))

                    self._test_writer.add_summary(summary_, step)
                    self._model._policy.save(sess)

                if show_progress:
                    if baseline_flag:
                        print('>> Epoch {0:5}: loss = {1:3.6f}'.format(step, loss_))
                    else:
                        print('Epoch {0:5}: loss = {1:3.6f}\r'.format(step, loss_), end='')

                self._hooks_run(sess, step)

            self._hooks_teardown()

            return losses, rewards

    def _build_reward_graph(self):
        '''Builds total reward statistics ops.'''
        self.avg_total_reward, self.variance_total_reward = tf.nn.moments(self._model.total_reward, axes=[0])

    def _build_loss_graph(self) -> None:
        '''Builds the loss ops.'''
        self.batch_loss = tf.reduce_sum(self._model.surrogate_batch_cost, axis=1, name='batch_loss')
        self.loss = tf.reduce_mean(self.batch_loss, name='loss')

    def _build_regularization_loss_graph(self,
            kernel_regularizer: Optional[Callable[[tf.Tensor], tf.Tensor]] = None,
            bias_regularizer: Optional[Callable[[tf.Tensor], tf.Tensor]] = None) -> None:

        if kernel_regularizer is not None:
            kernels = tf.trainable_variables(r'.*/kernel:0$')
            for kernel in kernels:
                self.loss += kernel_regularizer(kernel)

        if bias_regularizer is not None:
            biases = tf.trainable_variables(r'.*/bias:0$')
            for bias in biases:
                self.loss += bias_regularizer(bias)

    def _build_optimization_graph(self, optimizer, learning_rate: float) -> None:
        '''Builds the training ops.'''
        self._optimizer = optimizer(learning_rate)
        self._grad_and_vars = self._optimizer.compute_gradients(self.loss)
        self._train_op = self._optimizer.apply_gradients(self._grad_and_vars)

    def _build_summary_graph(self):
        '''Builds the summary ops.'''
        tf.summary.scalar('loss', self.loss)
        tf.summary.scalar('avg_total_reward', self.avg_total_reward)

    def _build_debug_graph(self):
        if not self._debug:
            return

        # reward statistics
        self.stddev_total_reward = tf.sqrt(self.variance_total_reward)
        self.max_total_reward = tf.reduce_max(self._model.total_reward)
        self.min_total_reward = tf.reduce_min(self._model.total_reward)
        tf.summary.histogram('total_reward', self._model.total_reward)
        tf.summary.scalar('stddev_total_reward', self.stddev_total_reward)
        tf.summary.scalar('max_total_reward', self.max_total_reward)
        tf.summary.scalar('min_total_reward', self.min_total_reward)

        # gradient statistics
        for grad, var in self._grad_and_vars:
            grad_name = self._get_summary_name(grad.name)
            tf.summary.scalar(grad_name + '/grad_norm', tf.norm(grad))
            tf.summary.histogram(grad_name + '/grad', grad)

            var_name = self._get_summary_name(var.name)
            tf.summary.scalar(var_name + '/norm', tf.norm(var))
            tf.summary.histogram(var_name, var)

    def _get_summary_name(self, name):
        m1 = re.search(r'/([^/]+)/([^/]+)/LayerNorm/batchnorm/sub/', name)
        m2 = re.search(r'/([^/]+)/([^/]+)/LayerNorm/beta', name)
        if m1 or m2:
            m = m1 if m1 else m2
            return '{}/{}/LayerNorm/beta'.format(m.group(1), m.group(2))

        m1 = re.search(r'/([^/]+)/([^/]+)/LayerNorm/batchnorm/mul/', name)
        m2 = re.search(r'/([^/]+)/([^/]+)/LayerNorm/gamma', name)
        if m1 or m2:
            m = m1 if m1 else m2
            return '{}/{}/LayerNorm/gamma'.format(m.group(1), m.group(2))

        summary_name = ''

        m = re.search(r'hidden(\d+)', name)
        if m:
            summary_name += 'hidden{}'.format(m.group(1))
        else:
            m = re.search(r'output/([^/]+)/dense/', name)
            if m:
                summary_name += 'output/{}'.format(m.group(1))

        if re.search(r'(MatMul)|(kernel)', name):
            summary_name += '/kernel'
        elif re.search(r'(BiasAdd)|(bias)', name):
            summary_name += '/bias'

        return summary_name

    def _hooks_setup(self):
        if self._hooks is not None:
            for hook in self._hooks:
                hook.setup(self, self._model)

    def _hooks_run(self, sess, step):
        if self._hooks is not None:
            for hook in self._hooks:
                hook(sess, step)

    def _hooks_teardown(self):
        if self._hooks is not None:
            for hook in self._hooks:
                hook.teardown()
