import unittest
import numpy

import theano
from theano import tensor

from rstreams import RandomStreams
import distributions
from sample import rejection_sample, mh_sample, hybridmc_sample, mh2_sample
from rv import is_rv, is_raw_rv, full_log_likelihood, lpdf
import for_theano
from for_theano import evaluate, ancestors, infer_shape, memoized

import pylab

def test_dirichlet():
    R = RandomStreams(234)
    n = R.dirichlet(alpha=numpy.ones(10,), draw_shape=(5,))
    
    f = theano.function([], n)
    
    assert f().shape == (5, 10)


def test_multinomial():
    R = RandomStreams(234)
    n = R.multinomial(5, numpy.ones(5,)/5, draw_shape=(2,))
    
    f = theano.function([], n)
    
    assert f().shape == (2, 5)


class TestBasicBinomial(unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)
        p = 0.5
        self.A = s_rng.binomial(1, p)
        self.B = s_rng.binomial(1, p)
        self.C = s_rng.binomial(1, p)
        self.D = self.A+self.B+self.C
        self.condition = tensor.ge(self.D, 2)

    def test_rejection_sampler(self):
        sample, updates = rejection_sample([self.A, self.B, self.C], self.condition)

        # create a runnable function
        sampler = theano.function(inputs=[], outputs = sample, updates = updates)

        # generate some data
        data = []
        for i in range(100):
            data.append(sampler())

        # plot histogram
        pylab.hist(numpy.asarray(data))
        pylab.show()

    def test_rejection_sampler_no_cond(self):
        sample, updates = rejection_sample([self.A, self.B, self.C])

        # create a runnable function
        sampler = theano.function(inputs=[], outputs = sample, updates = updates)

        # generate some data
        data = []
        for i in range(100):
            data.append(sampler())

        # plot histogram
        pylab.hist(numpy.asarray(data))
        pylab.show()


class TestQuantizedLogNormalMixture(unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)
        self.weights = tensor.dvector()
        self.mus = tensor.dvector()
        self.sigmas = tensor.dvector()

    def test_draw_1(self):
        q = self.s_rng.quantized_lognormal_mixture(
                self.weights,
                self.mus,
                self.sigmas,
                step=2)
        f = theano.function([self.weights, self.mus, self.sigmas],
                q)
        assert f([1.0], [0.0], [0.01]) == 2.0
        assert f([0.5, 0.5], [0.0, 0.0], [0.01, 0.001]) == 2.0

    def test_draw_0(self):
        q = self.s_rng.quantized_lognormal_mixture(
                self.weights,
                self.mus,
                self.sigmas,
                step=2,
                draw_shape = (0,))
        f = theano.function([self.weights, self.mus, self.sigmas],
                q)
        assert list(f([1.0], [0.0], [0.01])) == []
        assert list(f([0.5, 0.5], [0.0, 0.0], [0.01, 0.001])) == []



# first example: http://projects.csail.mit.edu/church/wiki/Learning_as_Conditional_Inference
class TestCoin(unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)

        self.fair_prior = 0.999
        self.fair_coin = s_rng.binomial(1, self.fair_prior)
        
        make_coin = lambda x: s_rng.binomial((4,), 1, x)    
        self.coin = make_coin(tensor.switch(self.fair_coin > 0.5, 0.5, 0.95))

        self.data = tensor.as_tensor_variable([[1, 1, 1, 1]])
        
    def test_tt(self):
        sample, updates = rejection_sample([self.fair_coin,], tensor.eq(tensor.sum(tensor.eq(self.coin, self.data)), 5))
        sampler = theano.function([], sample, updates=updates)
        
        # TODO: this is super-slow, how can bher do this fast?
        for i in range(100):
            print sampler()


class TestCoin2(): #unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)

        self.repetitions = 100        
        self.coin_weight = s_rng.uniform(low=0, high=1)
        self.coin = s_rng.binomial((self.repetitions,), 1, self.coin_weight)
        
    def test_tt(self):
        true_sampler = theano.function([self.coin_weight], self.coin)

        sample, ll, updates = mh_sample(self.s_rng, [self.coin_weight])
        sampler = theano.function([self.coin], sample, updates=updates)

        for i in range(100):
            print sampler(true_sampler(0.9))


class TestGMM(unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)

        self.p = tensor.scalar()
        self.m1 = tensor.scalar() 
        self.m2 = tensor.scalar() 
        self.v = tensor.scalar() 
        
        self.C = s_rng.binomial(1, p)
        self.m = tensor.switch(self.C, self.m1, self.m2)
        self.D = s_rng.normal(self.m, self.v)        
    
        self.D_data = tensor.as_tensor_variable([1, 1.2, 3, 3.4])
        
    def test_tt(self):
        RVs = dict([(self.D, self.D_data)])
        lik = full_log_likelihood(RVs)
        
        lf = theano.function([self.m1, self.m2, self.C], lik)
        
        print lf(1,3,0)
        print lf(1,3,1)

        # EM:
        #     E-step:
        #         C = expectation p(C | data, params)
        #     M-step:
        #         params = argmax p(params | C, data)
        #
        # MCMC (Gibbs):
        #     p(params | data, C)
        #     p(C | data, params)


class TestHierarchicalNormal(): #unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)
        a = 0.0
        b = 1.0
        c = 1.5
        d = 2.0

        self.M = s_rng.normal(a, b)
        self.V = s_rng.normal(c, d)
        self.V_ = abs(self.V) + .1
        self.X = s_rng.normal((4,), self.M, self.V_)

        self.X_data = tensor.as_tensor_variable([1, 2, 3, 2.4])

    def test_sample_gets_all_rvs(self):
        outs, dct = sample(self.s_rng, [self.X], ())
        assert outs == [self.X]
        assert len(dct) == 3

    def test_sample_can_be_generated(self):
        outs, dct = sample(self.s_rng, [self.X], ())
        f = theano.function([], [dct[self.X], dct[self.M],
            dct[self.V.owner.inputs[0]]])
        x0, m0, v0 = f()
        x1, m1, v1 = f()
        assert not numpy.any(x0 == x1)
        assert x0.shape == (4,)
        assert m0.shape == ()
        assert v1.shape == ()
        print x0, m0, v0

    def test_likelihood(self):
        outs, obs = sample(self.s_rng, [self.X], ())

        lik = likelihood(obs)

        f = theano.function([], lik)

        print f()

    def test_mh_sample(self):
        sample, ll, updates = mh_sample(self.s_rng, [self.M, self.V], observations={self.X: self.X_data}, lag = 100)
        sampler = theano.function([], sample, updates=updates)
        
        data = []
        for i in range(100):
            print i
            data.append(sampler())
        
        pylab.subplot(211)
        pylab.hist(numpy.asarray(data)[:,0])
        pylab.subplot(212)
        pylab.hist(numpy.asarray(data)[:,1])
        pylab.show()


class Fitting1D(unittest.TestCase):
    def setUp(self):
        self.obs = tensor.as_tensor_variable(
                numpy.asarray([0.0, 1.01, 0.7, 0.65, 0.3]))
        self.rstream = RandomStreams(234)
        self.n = self.rstream.normal()
        self.u = self.rstream.uniform()

    def test_normal_ml(self):
        up = self.rstream.ml(self.n, self.obs)
        p = self.rstream.params(self.n)
        f = theano.function([], [up[p[0]], up[p[1]]])
        m,v = f()
        assert numpy.allclose([m,v], [.532, 0.34856276335])

    def test_uniform_ml(self):
        up = self.rstream.ml(self.u, self.obs)
        p = self.rstream.params(self.u)
        f = theano.function([], [up[p[0]], up[p[1]]])
        l,h = f()
        assert numpy.allclose([l,h], [0.0, 1.01])


class TestHMM(): #unittest.TestCase):
    def setUp(self):
        s_rng = self.s_rng = RandomStreams(23424)

        self.nr_states = 5
        self.nr_obs = 3
        
        self.observation_model = memoized(lambda state: s_rng.dirichlet([1]*self.nr_obs))
        self.transition_model = memoized(lambda state: s_rng.dirichlet([1]*self.nr_states))
        
        self.transition = lambda state: s_rng.multinomial(1, self.tranisition_model(state))
        self.observation = lambda state: s_rng.multinomial(1, self.observation_model(state))
        
        def transition(obs, state):
            return [self.observation(state), self.transition(state)] ,{}, until(state == numpy.asarray([0,0,0,0,1])) 
            
        [self.sampled_words, self.sampled_states], updates = scan([], [obs, state])
        
    def test(self):
        print evaluate(self.sample_words([1,0,0,0,0]))


class TestGMM1(unittest.TestCase):
    def setUp(self):
        R = RandomStreams(234)
        weights = tensor.dvector()
        mus = tensor.dvector()
        sigmas = tensor.dvector()
        draw_shape = tensor.ivector()
        xsca = R.GMM1(weights, mus, sigmas, draw_shape=draw_shape, ndim=0)
        xvec = R.GMM1(weights, mus, sigmas, draw_shape=draw_shape, ndim=1)
        xmat = R.GMM1(weights, mus, sigmas, draw_shape=draw_shape, ndim=2)

        self.__dict__.update(locals())
        del self.self

    def test1(self):
        assert self.xsca.ndim == 0
        assert self.xvec.ndim == 1
        assert self.xmat.ndim == 2

        assert self.xsca.dtype == 'float64'
        assert self.xvec.dtype == 'float64'
        assert self.xmat.dtype == 'float64'

    def test_mu_is_used_correctly(self):
        f = theano.function(
                [self.weights, self.mus, self.sigmas, self.draw_shape],
                self.xsca)
        assert numpy.allclose(10, f([1], [10.0], [0.0000001], []))

    def test_sigma_is_used_correctly(self):
        f = theano.function(
                [self.weights, self.mus, self.sigmas, self.draw_shape],
                self.xvec)
        samples = f([1], [0.0], [10.0], [1000])
        assert 9 < numpy.std(samples) < 11

    def test_mus_make_variance(self):
        f = theano.function(
                [self.weights, self.mus, self.sigmas, self.draw_shape],
                self.xvec)

        samples = f([.5, .5], [0.0, 1.0], [0.000001, 0.000001], [1000])
        print samples.shape
        #import matplotlib.pyplot as plt
        #plt.hist(samples)
        #plt.show()
        assert .45 < numpy.mean(samples) < .55, numpy.mean(samples)
        assert .2 < numpy.var(samples) < .3, numpy.var(samples)

    def test_weights(self):
        f = theano.function(
                [self.weights, self.mus, self.sigmas, self.draw_shape],
                self.xvec)

        samples = f([.9999, .0001], [0.0, 1.0], [0.000001, 0.000001], [1000])
        assert samples.shape == (1000,)
        #import matplotlib.pyplot as plt
        #plt.hist(samples)
        #plt.show()
        assert -.001 < numpy.mean(samples) < .001, numpy.mean(samples)
        assert numpy.var(samples) < .0001, numpy.var(samples)

    def test_mat_output(self):
        f = theano.function(
                [self.weights, self.mus, self.sigmas, self.draw_shape],
                self.xmat)

        samples = f([.9999, .0001], [0.0, 1.0], [0.000001, 0.000001], [40, 20])
        assert samples.shape == (40, 20)
        assert -.001 < numpy.mean(samples) < .001, numpy.mean(samples)
        assert numpy.var(samples) < .0001, numpy.var(samples)

    def test_lpdf_scalar_one_component(self):
        xval = tensor.dscalar()
        ll = lpdf(self.xsca, xval)
        assert ll.ndim == 0, ll.type
        f = theano.function(
                [xval, self.weights, self.mus, self.sigmas, self.draw_shape],
                ll)
        llval = f(1.0, # x
                [1.],  # weights
                [1.0], # mu
                [2.0], # sigma
                [] # shape
                )
        assert llval.shape == ()
        assert numpy.allclose(llval,
                numpy.log(1.0 / numpy.sqrt(2 * numpy.pi * 2.0**2)))

    def test_lpdf_scalar_N_components(self):
        xval = tensor.dscalar()
        ll = lpdf(self.xsca, xval)
        assert ll.ndim == 0, ll.type
        f = theano.function(
                [xval, self.weights, self.mus, self.sigmas, self.draw_shape],
                ll)
        llval = f(1.0, # x
                [0.25, 0.25, .5],  # weights
                [0.0, 1.0, 2.0], # mu
                [1.0, 2.0, 5.0], # sigma
                [] # shape
                )

        a = (.25 / numpy.sqrt(2 * numpy.pi * 1.0 ** 2)
                * numpy.exp(-.5 * (1.0)**2))
        a += (.25 / numpy.sqrt(2 * numpy.pi * 2.0 ** 2))
        a += (.5 /  numpy.sqrt(2 * numpy.pi * 5.0 ** 2)
                * numpy.exp(-.5 * (1.0 / 5.0) ** 2))

    def test_lpdf_vector_N_components(self):
        xval = tensor.dvector()
        ll = lpdf(self.xvec, xval)
        assert ll.ndim == 1, ll.type
        f = theano.function(
                [xval, self.weights, self.mus, self.sigmas],
                ll)
        llval = f([1.0, 0.0],     # x
                [0.25, 0.25, .5], # weights
                [0.0, 1.0, 2.0],  # mu
                [1.0, 2.0, 5.0],  # sigma
                )

        # case x = 1.0
        a = (.25 / numpy.sqrt(2 * numpy.pi * 1.0 ** 2)
                * numpy.exp(-.5 * (1.0)**2))
        a += (.25 / numpy.sqrt(2 * numpy.pi * 2.0 ** 2))
        a += (.5 /  numpy.sqrt(2 * numpy.pi * 5.0 ** 2)
                * numpy.exp(-.5 * (1.0 / 5.0) ** 2))

        assert llval.shape == (2,)
        assert numpy.allclose(llval[0], numpy.log(a))


        # case x = 0.0
        a = (.25 / numpy.sqrt(2 * numpy.pi * 1.0 ** 2))
        a += (.25 / numpy.sqrt(2 * numpy.pi * 2.0 ** 2)
                * numpy.exp(-.5 * (1.0 / 2.0) ** 2))
        a += (.5 /  numpy.sqrt(2 * numpy.pi * 5.0 ** 2)
                * numpy.exp(-.5 * (2.0 / 5.0) ** 2))
        assert numpy.allclose(llval[1], numpy.log(a))

    def test_lpdf_matrix_N_components(self):
        xval = tensor.dmatrix()
        ll = lpdf(self.xmat, xval)
        assert ll.ndim == 2, ll.type
        f = theano.function(
                [xval, self.weights, self.mus, self.sigmas],
                ll)
        llval = f([[1.0, 0.0, 0.0], [0, 0, 1]], # x
                [0.25, 0.25, .5],  # weights
                [0.0, 1.0, 2.0], # mu
                [1.0, 2.0, 5.0], # sigma
                )

        a = (.25 / numpy.sqrt(2 * numpy.pi * 1.0 ** 2)
                * numpy.exp(-.5 * (1.0)**2))
        a += (.25 / numpy.sqrt(2 * numpy.pi * 2.0 ** 2))
        a += (.5 /  numpy.sqrt(2 * numpy.pi * 5.0 ** 2)
                * numpy.exp(-.5 * (1.0 / 5.0) ** 2))

        assert llval.shape == (2,3)
        assert numpy.allclose(llval[0,0], numpy.log(a))
        assert numpy.allclose(llval[1,2], numpy.log(a))


        a = (.25 / numpy.sqrt(2 * numpy.pi * 1.0 ** 2))
        a += (.25 / numpy.sqrt(2 * numpy.pi * 2.0 ** 2)
                * numpy.exp(-.5 * (1.0 / 2.0)**2))
        a += (.5 /  numpy.sqrt(2 * numpy.pi * 5.0 ** 2)
                * numpy.exp(-.5 * (2.0 / 5.0) ** 2))

        assert numpy.allclose(llval[0,1], numpy.log(a))
        assert numpy.allclose(llval[0,2], numpy.log(a))
        assert numpy.allclose(llval[1,0], numpy.log(a))
        assert numpy.allclose(llval[1,1], numpy.log(a))

    # XXX: make sure lpdf calculation includes logsum

    if 0:
        def test_illustrate(self):
            f = theano.function(
                    [self.weights, self.mus, self.sigmas, self.draw_shape],
                    self.xvec)

            samples = f(#numpy.arange(16)/numpy.arange(16).sum(),
                    numpy.ones(16)/16,
                    numpy.arange(16),
                    #.02 * (numpy.arange(16)+1),
                    .2 * numpy.ones(16),
                    [10000])
            import matplotlib.pyplot as plt
            plt.hist(samples, bins=100)
            plt.show()
