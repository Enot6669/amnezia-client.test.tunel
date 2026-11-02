from conan import ConanFile
from conan.tools.layout import basic_layout
from conan.tools.files import download, copy
from conan.errors import ConanInvalidConfiguration

import os


class WinSplitTunnel(ConanFile):
    name = "win-split-tunnel"
    version = "1.3.0.0"
    settings = "os", "arch"

    # Pin the signed upstream artifacts independently of the driver source tag.
    _BINARIES_COMMIT = "5b6f46cde692acb77ee74b37b9fd3f1678c45a52"

    @property
    def _arch(self):
        return {
            "x86_64": "x86_64",
            "armv8": "aarch64"
        }.get(str(self.settings.arch))

    @property
    def _target(self):
        return f"{self._arch}-pc-windows-msvc"

    def layout(self):
        basic_layout(self)

    def validate(self):
        if not str(self.settings.os).startswith("Windows"):
            raise ConanInvalidConfiguration(
                f"{self.name} v{self.version} supports only Windows"
            )
        if self._arch is None:
            raise ConanInvalidConfiguration(
                f"{self.name} v{self.version} does not support {self.settings.arch}"
            )

    def build(self):
        url = (
            "https://raw.githubusercontent.com/mullvad/"
            f"mullvadvpn-app-binaries/{self._BINARIES_COMMIT}/"
            f"{self._target}/split-tunnel"
        )

        # SHA-256 values were calculated from the exact files at the pinned
        # binaries commit. Keep the catalog and INF alongside the signed SYS.
        files = [
            ("mullvad-split-tunnel.cat", "c599926a0327d7ae06b534f4cd039db30392e1897bb9d03e4fec3631744a4e6d"),
            ("mullvad-split-tunnel.inf", "3dd5905e5fb98d61a942a33e8c9a5ba07c3a2de1e4f319e1fec3e54df6591608"),
            ("mullvad-split-tunnel.sys", "10cf25bbcfe51fd663a1fec88a98e9b858f3a579589bb2ec496b66e4fdd1b201"),
        ]

        for name, sha256 in files:
            download(
                self,
                f"{url}/{name}",
                os.path.join("prebuilt", name),
                sha256=sha256
            )

    def package(self):
        copy(self, "*", src="prebuilt", dst=os.path.join(self.package_folder, "bin"))

    def package_info(self):
        self.cpp_info.set_property("cmake_target_name", "mullvad::win-split-tunnel")
        self.cpp_info.set_property("cmake_extra_variables", {
            "WIN_SPLIT_TUNNEL_BIN": os.path.join(self.package_folder, "bin").replace("\\", "/")
        })
