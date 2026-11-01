#!/usr/bin/env python3
"""Django management entry point."""

import os
import sys

from common.environment import load_env_defaults

if __name__ == "__main__":
    load_env_defaults()
    default_environment = "test" if sys.argv[1:2] == ["test"] else "development"
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", f"config.settings.{default_environment}"
    )
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
