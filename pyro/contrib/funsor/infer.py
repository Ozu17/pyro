# Copyright Contributors to the Pyro project.
# SPDX-License-Identifier: Apache-2.0

import funsor

from pyro.infer import ELBO
from pyro.poutine.util import prune_subsample_sites

from pyro.contrib.funsor import to_data, to_funsor
from pyro.contrib.funsor.enum_messenger import EnumMessenger, ReplayMessenger, TraceMessenger

funsor.set_backend("torch")


class TraceEnum_ELBO(ELBO):

    def _get_trace(self, *args, **kwargs):
        raise ValueError("shouldn't be here")

    def differentiable_loss(self, model, guide, *args, **kwargs):
        # currently supports guide enumeration only, not mixed enumeration
        with TraceMessenger() as guide_tr, EnumMessenger(-self.max_plate_nesting-1):
            guide(*args, **kwargs)
        with TraceMessenger() as model_tr, ReplayMessenger(trace=guide_tr.trace):
            model(*args, **kwargs)
        factors, measures, measure_vars, plate_vars = [], [], frozenset(), frozenset()
        for role, trace in zip(("model", "guide"), (model_tr.trace, guide_tr.trace)):
            trace = prune_subsample_sites(trace)
            for name, node in trace.nodes.items():
                if role == "model":
                    factors.append(node["infer"]["funsor_log_prob"])
                elif role == "guide":
                    factors.append(-node["infer"]["funsor_log_prob"])
                    measures.append(node["infer"]["funsor_log_measure"])
                plate_vars |= frozenset(f.name for f in node["cond_indep_stack"] if f.vectorized)
                measure_vars |= frozenset(node["infer"]["funsor_log_prob"].inputs) - plate_vars

        # TODO support model enumeration
        # compute actual loss
        elbo = to_funsor(0., funsor.reals())
        # TODO get this optimizer call right
        with funsor.interpreter.interpretation(funsor.optimizer.optimize), funsor.memoize.memoize():
            for factor in factors:
                elbo = elbo + funsor.sum_product.sum_product(
                    funsor.ops.add, funsor.ops.mul,
                    [lm.exp() for lm in measures] + [factor],
                    eliminate=measure_vars | frozenset(factor.inputs) | plate_vars,
                    plates=plate_vars & frozenset(factor.inputs)
                )

        return to_data(elbo)


class TraceTMC_ELBO(ELBO):

    def _get_trace(self, *args, **kwargs):
        raise ValueError("shouldn't be here")

    def differentiable_loss(self, model, guide, *args, **kwargs):
        with TraceMessenger() as guide_tr, EnumMessenger(-self.max_plate_nesting-1):
            guide(*args, **kwargs)
        with TraceMessenger() as model_tr, EnumMessenger(-self.max_plate_nesting-1), \
                ReplayMessenger(trace=guide_tr.trace):
            model(*args, **kwargs)
        factors, measures, sum_vars, plate_vars = [], [], frozenset(), frozenset()
        for role, trace in zip(("model", "guide"), (model_tr.trace, guide_tr.trace)):
            trace = prune_subsample_sites(trace)
            for name, node in trace.nodes.items():
                if node["type"] != "sample":
                    continue
                if role == "model":
                    factors.append(node["infer"]["funsor_log_prob"])
                    if name not in guide_tr.trace.nodes and not node["is_observed"]:
                        measures.append(node["infer"]["funsor_log_measure"])
                elif role == "guide":
                    factors.append(-node["infer"]["funsor_log_prob"])
                    measures.append(node["infer"]["funsor_log_measure"])
                plate_vars |= frozenset(f.name for f in node["cond_indep_stack"] if f.vectorized)
                sum_vars |= frozenset(node["infer"]["funsor_log_prob"].inputs) - plate_vars

        with funsor.interpreter.interpretation(funsor.terms.lazy):
            elbo = funsor.sum_product.sum_product(
                funsor.ops.logaddexp, funsor.ops.add,
                measures + factors, eliminate=sum_vars | plate_vars, plates=plate_vars
            )
        return to_data(funsor.optimizer.apply_optimizer(elbo))
