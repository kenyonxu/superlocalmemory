"""Bundle the shared bridge from a checkout; use the bundled copy from an sdist."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        # https://hatch.pypa.io/latest/plugins/build-hook/custom/
        # https://hatch.pypa.io/latest/plugins/build-hook/reference/
        package = self.config["package"]
        bundled = Path(self.root) / package / "_engine_bridge.py"
        source = bundled if bundled.is_file() else Path(self.root).parent / "_engine_bridge.py"
        if not source.is_file():
            raise FileNotFoundError("Adapter engine bridge is missing from the build source")
        if source != bundled:
            build_data["force_include"][str(source)] = f"{package}/_engine_bridge.py"
