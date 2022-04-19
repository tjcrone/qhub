from qhub.initialize import Initialize, render_config
from qhub.schema import ProviderEnum
from qhub.utils import yaml


def create_init_subcommand(subparser):
    subparser = subparser.add_parser("init")
    Initialize.schema_to_parser(subparser)
    subparser.set_defaults(func=handle_init)


def handle_init(args):
    Initialize(**args.__dict__).to_main().write()
