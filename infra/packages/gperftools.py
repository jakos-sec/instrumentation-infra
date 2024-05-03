import os
import shutil
from typing import Iterator

from ..context import Context
from ..package import Package
from ..util import apply_patch, download, run
from .gnu import AutoMake


class LibUnwind(Package):
    """
    :identifier: libunwind-<version>
    :param version: version to download
    """

    def __init__(self, version: str, patches: list[str] = []):
        self.version = version
        self.patches = patches

    def ident(self) -> str:
        return "libunwind-" + self.version

    def is_fetched(self, ctx: Context) -> bool:
        return os.path.exists("src")

    def fetch(self, ctx: Context) -> None:
        urlbase = "http://download.savannah.gnu.org/releases/libunwind/"
        dirname = self.ident()
        tarname = dirname + ".tar.gz"
        download(ctx, urlbase + tarname)
        run(ctx, ["tar", "-xf", tarname])
        shutil.move(dirname, "src")
        os.remove(tarname)

    def is_built(self, ctx: Context) -> bool:
        return os.path.exists("obj/src/.libs/libunwind.so")

    def _apply_patches(self, ctx: Context) -> None:
        os.chdir(self.path(ctx, "src"))
        config_root = os.path.dirname(os.path.abspath(__file__))
        for path in self.patches:
            if "/" not in path:
                path = f"{config_root}/{path}.patch"
            if apply_patch(ctx, path, 1):
                ctx.log.warning(f"applied patch {path} to libunwind directory")
        os.chdir(self.path(ctx))

    def build(self, ctx: Context) -> None:
        self._apply_patches(ctx)

        os.makedirs("obj", exist_ok=True)
        os.chdir("obj")
        if not os.path.exists("Makefile"):
            run(ctx, ["../src/configure", "--prefix=" + self.path(ctx, "install")])
        run(ctx, f"make -j{ctx.jobs}")

    def is_installed(self, ctx: Context) -> bool:
        return os.path.exists("install/lib/libunwind.so")

    def install(self, ctx: Context) -> None:
        os.chdir("obj")
        run(ctx, "make install")

    def configure(self, ctx: Context) -> None:
        ctx.ldflags += ["-L" + self.path(ctx, "install/lib"), "-lunwind"]


class Gperftools(Package):
    """
    :identifier: gperftools-<version>
    :param commit: git branch/commit to check out after cloning
    :param libunwind_version: libunwind version to use
    :param patches: optional patches to apply before building
    """

    def __init__(self, commit: str, libunwind_version: str = "1.4-rc1", patches: list[str] = []):
        self.commit = commit
        self.libunwind = LibUnwind(libunwind_version)
        self.patches = patches

    def ident(self) -> str:
        return "gperftools-" + self.commit

    def dependencies(self) -> Iterator[Package]:
        yield AutoMake.default()
        yield self.libunwind

    def is_fetched(self, ctx: Context) -> bool:
        return os.path.exists("src")

    def fetch(self, ctx: Context) -> None:
        run(ctx, "git clone https://github.com/gperftools/gperftools.git src")
        os.chdir("src")
        run(ctx, ["git", "checkout", self.commit])

    def is_built(self, ctx: Context) -> bool:
        return os.path.exists("obj/.libs/libtcmalloc.so")

    def _apply_patches(self, ctx: Context) -> None:
        os.chdir(self.path(ctx, "src"))
        config_root = os.path.dirname(os.path.abspath(__file__))
        for path in self.patches:
            if "/" not in path:
                path = f"{config_root}/{path}.patch"
            if apply_patch(ctx, path, 1):
                ctx.log.warning(f"applied patch {path} to gperftools directory")
        os.chdir(self.path(ctx))

    def build(self, ctx: Context) -> None:
        self._apply_patches(ctx)

        if not os.path.exists("src/configure") or not os.path.exists("src/INSTALL"):
            os.chdir("src")
            run(ctx, "autoreconf -vfi")
            self.goto_rootdir(ctx)

        os.makedirs("obj", exist_ok=True)
        os.chdir("obj")
        if not os.path.exists("Makefile"):
            prefix = self.path(ctx, "install")
            run(
                ctx,
                [
                    "../src/configure",
                    "CPPFLAGS=-I" + self.libunwind.path(ctx, "install/include"),
                    "LDFLAGS=-L" + self.libunwind.path(ctx, "install/lib"),
                    "--prefix=" + prefix,
                ],
            )
        run(ctx, f"make -j{ctx.jobs}")

    def is_installed(self, ctx: Context) -> bool:
        return os.path.exists("install/lib/libtcmalloc.so")

    def install(self, ctx: Context) -> None:
        os.chdir("obj")
        run(ctx, "make install")

    def configure(self, ctx: Context) -> None:
        """
        Set build/link flags in **ctx**. Should be called from the
        ``configure`` method of an instance.

        Sets the necessary ``-I/-L/-l`` flags, and additionally adds
        ``-fno-builtin-{malloc,calloc,realloc,free}`` to CFLAGS.

        :param ctx: the configuration context
        """
        self.libunwind.configure(ctx)
        cflags = ["-fno-builtin-" + fn for fn in ("malloc", "calloc", "realloc", "free")]
        cflags += ["-I", self.path(ctx, "install/include/gperftools")]
        ctx.cflags += cflags
        ctx.cxxflags += cflags
        ctx.ldflags += ["-L" + self.path(ctx, "install/lib"), "-ltcmalloc", "-lpthread"]
