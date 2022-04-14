from abc import ABC
from contextlib import ExitStack
from functools import partialmethod
from logging import getLogger
from typing import Any

from attr import field
from qhub.provider import terraform
from pathlib import Path
from pydantic import BaseModel, BaseSettings, Field
from .stages import tf_objects

from qhub.utils import keycloak_provider_context, kubernetes_provider_context


class BaseModel(BaseModel):
    def get_logger(self):
        return getLogger(type(self).__name__)

    def log(self, method, msg):
        return getattr(self.get_logger(), method)(msg)

    for k in "debug info warning error critical".split():
        locals()[k] = partialmethod(log, k)


class Provider:
    pass


class Local(Provider):
    pass


class GoogleCloud(Provider):
    alias = "gcp"


class DigitalOcean(Provider):
    alias = "do"


class AmazonWebServices(Provider):
    alias = "aws"


class Azure(Provider):
    alias = "azure"


class All(Local, GoogleCloud, AmazonWebServices, Azure):
    pass


# qhub uses the concept of stages to maintain contain context through the deployment of complex infrastructure.
# stage are dependent on each other and the Stages captures their cooperative effects
class Stages(BaseModel):
    stages: list = Field(default_factory=list)
    data: dict = Field(default_factory=dict)
    directory: Path = Path("stages")
    stack: ExitStack = Field(default_factory=ExitStack)

    # this should be the qhub config
    class Config(BaseSettings):
        provider: str = "aws"

    config: Config = Field(default_factory=Config)

    def get_stages(self):
        yield from map(self.get_stage, self.stages)

    def get_stage(self, stage):
        return stage

    # render_templates
    def render(self):
        with self.stack as stack:
            for i, stage in enumerate(self.get_stages()):
                stage = stage(directory=self.directory, parent=self)
                stack.enter_context(stage)
                stage.render()

    # guide_install
    def deploy(self):
        with self.stack as stack:
            for i, stage in enumerate(self.get_stages()):
                stage = stage(directory=self.directory, parent=self)
                stack.enter_context(stage)
                stage.deploy()
                stage.check()

    # destory terra firma foreva
    def destroy(self):
        with self.stack as stack:
            for i, stage in enumerate(self.get_stages()):
                stage = stage(directory=self.directory, parent=self)
                stack.enter_context(stage)
                stage.destroy()


# a task is an action that effects the file system.
# its state and contents can be measured.
class Task(BaseModel):
    name: str
    directory: Path
    parent: Stages

    def __enter__(self):
        pass

    def __exit__(self, *e):
        pass


# a task to configure the qhub configuration file
class Configuration(Task):
    pass


# a stage is a special task that knows how to perform terraform actions
# its api includes deploy, destroy and validation
class Stage(Task):
    def input_vars(self):
        return {}

    def state_imports(self):
        return {}

    @property
    def target(self):
        return self.parent.directory / self.directory / self.parent.config.provider

    def render(self):
        self.parent.data.setdefault(self.directory, {})
        # make sure derived renders short circuit
        self.parent.data[self.directory] = self.tf()

    def check(self):
        pass

    def deploy(self, init=True, imports=False, apply=True, destroy=False):
        return terraform.deploy(
            self.target,
            init,
            imports,
            apply,
            destroy,
            self.input_vars(),
            self.state_imports(),
        )

    def destroy(
        self, ignore_errors=False, imports=True, init=True, destroy=True, apply=False
    ):
        try:
            self.deploy(init, imports, apply, destroy)
        except terraform.TerraformException as e:
            if ignore_errors:
                raise e
            return False
        return True


from qhub.stages import checks, state_imports, input_vars


class TerraformStateBase(Stage):
    directory: Path = Path("01-terraform-state")

    def deploy(self) -> dict:
        return {}


class TerraformStateCloud(TerraformStateBase, DigitalOcean, AmazonWebServices, Azure):
    def deploy(self):
        return self.deploy(
            terraform_import=True,
            directory=self.target,
            input_vars=self.input_vars(),
            state_imports=self.state_imports(),
        )

    def tf(self):
        return tf_objects.stage_01_terraform_state(self.parent.config)

    def state_imports(self):
        return state_imports.stage_01_terraform_state(self.parent.data, self.config)

    def input_vars(self):
        return input_vars.stage_01_terraform_state(self.parent.data, self.config)


class TerraformStateLocal(TerraformStateBase, Local):
    pass


class Infrastructure(Stage, All):
    directory: Path = Path("02-infrastructure")

    def tf(self):
        return tf_objects.stage_02_infrastructure(self.parent.config)

    def check(self):
        checks.stage_02_infrastructure(self.parent.data, self.config)

    def input_vars(self):
        return input_vars.stage_02_infrastructure(self.parent.data, self.config)


class KubernetesInitialize(Stage, All):
    def __enter__(self):
        data = self.parent.data["stage/02-infrastructure"]
        data = data["kubernetes_credentials"]["value"]
        self.parent.stack.enter_context(kubernetes_provider_context(data))

    def tf(self):
        return tf_objects.stage_03_kubernetes_initialize(self.parent.config)

    def check(self):
        checks.stage_03_kubernetes_initialize(self.parent.data, self.config)

    def input_vars(self):
        return input_vars.stage_03_kubernetes_initialize(self.parent.data, self.config)


class KubernetesIngress(Stage, All):
    def tf(self):
        return tf_objects.stage_04_kubernetes_ingress(self.parent.config)

    def check(self):
        checks.stage_04_kubernetes_ingress(self.parent.data, self.config)

    def input_vars(self):
        return input_vars.stage_04_kubernetes_ingress(self.parent.data, self.config)


class KubernetesKeyCloak(Stage, All):
    def tf(self):
        return tf_objects.stage_05_kubernetes_keycloak(self.parent.config)

    def check(self):
        checks.stage_05_kubernetes_keycloak(self.parent.data, self.config)

    def input_vars(self):
        return input_vars.stage_05_kubernetes_keycloak(self.parent.data, self.config)


class KubernetesKeyCloackConfiguration(Stage, All):
    def __enter__(self):
        data = self.parent.data["stages/05-kubernetes-keycloak"]
        data = data["keycloak_credentials"]["value"]
        self.parent.stack.enter_context(keycloak_provider_context(data))

    def tf(self):
        return tf_objects.stage_06_kubernetes_keycloak_configuration(self.parent.config)

    def input_vars(self):
        return input_vars.stage_06_kubernetes_keycloak_configuration(
            self.parent.data, self.config
        )

    def check(self):
        return checks.stage_06_kubernetes_keycloak_configuration(
            self.parent.data, self.config
        )


class KubernetesServices(Stage, All):
    def tf(self):
        return tf_objects.stage_07_kubernetes_services(self.parent.config)

    def input_vars(self):
        return input_vars.stage_07_kubernetes_services(self.parent.data, self.config)

    def check(self):
        return checks.stage_07_kubernetes_services(self.parent.data, self.config)


class QhubTfExtensions(Stage, All):
    def tf(self):
        return tf_objects.stage_07_kubernetes_services(self.parent.config)
