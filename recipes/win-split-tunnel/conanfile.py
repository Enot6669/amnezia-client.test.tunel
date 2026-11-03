from conan import ConanFile
from conan.tools.layout import basic_layout
from conan.tools.files import download, copy
from conan.errors import ConanInvalidConfiguration

import os
import subprocess
import sys
import zipfile
from pathlib import Path


class WinSplitTunnel(ConanFile):
    name = "win-split-tunnel"
    version = "1.3.0.0"
    settings = "os", "arch"
    exports_sources = "apply_reverse_mode_patch.py"

    # Build the exact upstream source revision with Amnezia's reverse-mode patch.
    # Do not fetch Mullvad's prebuilt signed SYS: it does not implement reverse mode.
    _SOURCE_COMMIT = "0a0eb97f67d1dbcb3d08bda66d3b24f465d95475"

    @property
    def _arch(self):
        return str(self.settings.arch)

    def layout(self):
        basic_layout(self)

    def validate(self):
        if not str(self.settings.os).startswith("Windows"):
            raise ConanInvalidConfiguration(
                f"{self.name} v{self.version} supports only Windows"
            )
        if self._arch != "x86_64":
            raise ConanInvalidConfiguration(
                "The Amnezia reverse-mode driver is currently built for Windows x64 only"
            )

    def build(self):
        source_url = (
            "https://github.com/mullvad/win-split-tunnel/archive/"
            f"{self._SOURCE_COMMIT}.zip"
        )
        os.makedirs(self.source_folder, exist_ok=True)
        archive = os.path.join(self.source_folder, "win-split-tunnel-source.zip")
        download(self, source_url, archive)

        with zipfile.ZipFile(archive, "r") as source_zip:
            source_zip.extractall(self.source_folder)

        source_root = Path(self.source_folder) / f"win-split-tunnel-{self._SOURCE_COMMIT}"
        if not (source_root / "src" / "mullvad-split-tunnel.vcxproj").is_file():
            candidates = list(Path(self.source_folder).glob("win-split-tunnel-*"))
            source_root = next(
                (candidate for candidate in candidates
                 if (candidate / "src" / "mullvad-split-tunnel.vcxproj").is_file()),
                None
            )
        if source_root is None or not (source_root / "src" / "mullvad-split-tunnel.vcxproj").is_file():
            raise ConanInvalidConfiguration(
                "Pinned Mullvad driver source archive does not contain the expected MSBuild project"
            )

        patcher = Path(self.export_sources_folder) / "apply_reverse_mode_patch.py"
        if not patcher.is_file():
            raise ConanInvalidConfiguration(f"Reverse-mode patcher is missing: {patcher}")
        subprocess.run(
            [sys.executable, str(patcher), str(source_root)],
            check=True
        )

        program_files_x86 = os.environ.get("ProgramFiles(x86)")
        if not program_files_x86:
            raise ConanInvalidConfiguration("ProgramFiles(x86) is not available on this Windows runner")
        vswhere = Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if not vswhere.is_file():
            raise ConanInvalidConfiguration(f"Visual Studio locator was not found: {vswhere}")

        vs_install = subprocess.run(
            [
                str(vswhere), "-latest", "-products", "*",
                "-requires", "Microsoft.Component.MSBuild",
                "-property", "installationPath"
            ],
            check=True, capture_output=True, text=True
        ).stdout.strip()
        if not vs_install:
            raise ConanInvalidConfiguration("Visual Studio MSBuild installation was not found")

        msbuild = Path(vs_install) / "MSBuild" / "Current" / "Bin" / "amd64" / "MSBuild.exe"
        if not msbuild.is_file():
            raise ConanInvalidConfiguration(f"64-bit MSBuild was not found: {msbuild}")

        project = source_root / "src" / "mullvad-split-tunnel.vcxproj"
        self.output.info("Building patched reverse-mode Windows x64 kernel driver")
        build_result = subprocess.run(
            [
                str(msbuild), str(project), "/m",
                "/p:Configuration=Release",
                "/p:Platform=x64",
                "/p:SignMode=Off",
                "/verbosity:normal"
            ],
            check=False,
            cwd=str(source_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        if build_result.stdout:
            for line in build_result.stdout.splitlines():
                self.output.info(f"MSBuild: {line}")
        if build_result.returncode != 0:
            raise ConanInvalidConfiguration(
                f"MSBuild failed with exit code {build_result.returncode}; "
                "see the MSBuild output above for the compiler error"
            )

        sys_candidates = sorted(
            source_root.rglob("mullvad-split-tunnel.sys"),
            key=lambda path: ("x64-release" not in str(path).lower(), len(str(path)))
        )
        if not sys_candidates:
            raise ConanInvalidConfiguration("MSBuild completed without producing mullvad-split-tunnel.sys")

        driver_output = sys_candidates[0].parent
        required_files = ("mullvad-split-tunnel.sys", "mullvad-split-tunnel.inf")
        package_input = Path(self.build_folder) / "driver-package"
        # Remove stale files from a previous build so the package cannot mix
        # outputs from different driver builds.
        if package_input.exists():
            import shutil
            shutil.rmtree(package_input)
        package_input.mkdir(parents=True, exist_ok=True)
        for filename in required_files:
            source_file = driver_output / filename
            if not source_file.is_file():
                raise ConanInvalidConfiguration(
                    f"Built driver package is missing required file: {source_file}"
                )
            (package_input / filename).write_bytes(source_file.read_bytes())

        # The unsigned development build may or may not emit a catalog.
        catalog = driver_output / "mullvad-split-tunnel.cat"
        if catalog.is_file():
            (package_input / catalog.name).write_bytes(catalog.read_bytes())
            self.output.info("Driver catalog found and copied")
        else:
            self.output.warning(
                "No mullvad-split-tunnel.cat was generated. "
                "This package is development-only and is not a complete "
                "Driver Store/PnP package; an unsigned SYS is not production-ready."
            )

        self.output.info(f"Using patched driver output from: {driver_output}")

    def package(self):
        copy(
            self,
            "*",
            src=os.path.join(self.build_folder, "driver-package"),
            dst=os.path.join(self.package_folder, "bin")
        )

    def package_info(self):
        self.cpp_info.set_property("cmake_target_name", "mullvad::win-split-tunnel")
        self.cpp_info.set_property("cmake_extra_variables", {
            "WIN_SPLIT_TUNNEL_BIN": os.path.join(self.package_folder, "bin").replace("\\", "/")
        })
