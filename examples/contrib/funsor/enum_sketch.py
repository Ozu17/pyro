# Copyright Contributors to the Pyro project.
# SPDX-License-Identifier: Apache-2.0

from pyro.poutine import Messenger

from pyro.contrib.funsor import to_funsor
from pyro.contrib.funsor.enum_messenger import EnumMessenger, TraceMessenger


def simple_ve_1(model, *args):

    import funsor; funsor.set_backend("torch")  # noqa: E702

    with TraceMessenger() as tr:
        with EnumMessenger():
            model(*args)

    log_joint = sum([site["infer"]["funsor_fn"](value=site["infer"]["funsor_value"])
                     for site in tr.trace.nodes.values()])
    return log_joint.reduce(funsor.ops.logaddexp)


class FunsorLogJointMessenger(Messenger):

    def __enter__(self):
        self.log_joint = to_funsor(0.)
        return super().__enter__()

    def _pyro_post_sample(self, msg):
        import funsor; funsor.set_backend("torch")  # noqa: E702
        with funsor.interpretation(funsor.terms.lazy):
            self.log_joint += msg["infer"].get("funsor_fn", to_funsor(msg["fn"]))(
                value=msg["infer"].get("funsor_value", to_funsor(msg["value"]))
            )


def simple_ve_2(model, *args):

    import funsor; funsor.set_backend("torch")  # noqa: E702

    with FunsorLogJointMessenger() as tr:
        with EnumMessenger():
            model(*args)

    with funsor.interpretation(funsor.optimizer.optimize):
        return tr.log_joint.reduce(funsor.ops.logaddexp)
