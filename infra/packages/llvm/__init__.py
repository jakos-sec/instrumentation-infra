import os
import shutil
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional

from ...context import Context
from ...package import Package
from ...util import apply_patch, download, run
from ..cmake import CMake
from ..gnu import AutoMake, Bash, BinUtils, CoreUtils, Make
from ..ninja import Ninja


class LLVM(Package):
    """
    LLVM dependency package. Includes the Clang compiler and optionally
    `compiler-rt <https://compiler-rt.llvm.org>`_ (which contains runtime
    support for ASan).

    Supports a number of patches to be passed as arguments, which are
    :func:`applied <util.apply_patch>` (with ``patch -p1``) before building. A
    patch in the list can either be a full path to a patch file, or the name of
    a built-in patch. Available built-in patches are:

    - **gold-plugins** (for 3.8.0/3.9.1/4.0.0/5.0.0/7.0.0): adds a ``-load``
      option to load passes from a shared object file during link-time
      optimizations, best used in combination with :class:`LLVMPasses`

    - **statsfilter** (for 3.8.0/3.9.1/5.0.0/7.0.0): adds ``-stats-only``
      option, which relates to ``-stats`` like ``-debug-only`` relates to
      ``-debug``

    - **lto-nodiscard-value-names** (for 7.0.0): preserves value names when
      producing bitcode for LTO, this is very useful when debugging passes

    - **safestack** (for 3.8.0): adds ``-fsanitize=safestack`` for old LLVM

    - **compiler-rt-typefix** (for 4.0.0): fixes a compiler-rt-4.0.0 bug to make
      it compile for recent glibc, is applied automatically if ``compiler_rt``
      is set

    :identifier: llvm-<version>
    :param version: the full LLVM version to download, like X.Y.Z
    :param compiler_rt: whether to enable compiler-rt
    :param patches: optional patches to apply before building
    :param build_flags: additional `build flags
                        <https://www.llvm.org/docs/CMake.html#options-and-variables>`_
                        to pass to cmake
    """

    # supported_versions = ('3.8.0', '3.9.1', '4.0.0', '5.0.0')
    binutils = BinUtils("2.38", gold=True)

    def __init__(
        self,
        version: str,
        compiler_rt: bool,
        commit: Optional[str] = None,
        lld: bool = False,
        patches: List[str] = [],
        build_flags: List[str] = [],
    ):
        # if version not in self.supported_versions:
        #    raise FatalError('LLVM version must be one of %s' %
        #            '/'.join(self.supported_versions))

        self.version = version
        self.compiler_rt = compiler_rt
        self.lld = lld
        self.patches = patches
        self.build_flags = build_flags
        self.commit = commit

        if compiler_rt and version == "4.0.0":
            patches.append("compiler-rt-typefix")

    def ident(self) -> str:
        suffix = "-lld" if self.lld else ""
        return "llvm-" + self.version + suffix

    def dependencies(self) -> Iterator[Package]:
        # TODO: prune these
        yield Bash("5.1.16")
        yield CoreUtils("9.1")
        yield self.binutils
        yield Make("4.3")
        yield AutoMake.default()
        yield CMake("3.28.6")
        yield Ninja("1.8.2")

    def fetch(self, ctx: Context) -> None:
        if self.commit is not None:
            run(
                ctx, ["git", "clone", "https://github.com/llvm/llvm-project.git", "src"]
            )
            os.chdir("src")
            run(ctx, ["git", "checkout", self.commit])
            return

        def get(repo: str, clonedir: str) -> None:
            basedir = os.path.dirname(clonedir)
            if basedir:
                os.makedirs(basedir, exist_ok=True)

            # url = 'http://llvm.org/svn/llvm-project/%s/trunk' % repo
            # run(ctx, ['svn', 'co', '-r' + ctx.params.commit, url, clonedir])

            dirname = f"{repo}-{self.version}.src"
            tarname = f"{dirname}.tar.xz"
            major_version = int(self.version.split(".")[0])

            if major_version >= 8:
                # use github now
                url_prefix = "https://github.com/llvm/llvm-project/releases/download"
                download(ctx, f"{url_prefix}/llvmorg-{self.version}/{tarname}")
            else:
                download(ctx, f"https://releases.llvm.org/{self.version}/{tarname}")

            run(ctx, ["tar", "-xf", tarname])
            shutil.move(dirname, clonedir)
            os.remove(tarname)

        major_version = int(self.version.split(".")[0])

        # starting with 9.0.1 the llvm-project is available completely
        # specifically with 15+ the separate tarballs are pretty broken
        if major_version >= 9:
            get("llvm-project", "src")
        else:
            # download and unpack sources
            get("llvm", "src")

            if major_version >= 8:
                get("clang", "src/tools/clang")
            else:
                get("cfe", "src/tools/clang")

            if self.compiler_rt:
                get("compiler-rt", "src/projects/compiler-rt")
            if self.lld:
                get("lld", "src/projects/lld")

    def build(self, ctx: Context) -> None:
        major_version = int(self.version.split(".")[0])
        # TODO: verify that any applied patches are in self.patches, error
        # otherwise

        # apply patches from the directory this file is in
        # do this in build() instead of fetch() to make sure patches are applied
        # with --force-rebuild
        os.chdir("src")
        config_path = os.path.dirname(os.path.abspath(__file__))
        for path in self.patches:
            original_dir = None
            if isinstance(path, tuple):
                # if path is a tuple then you can specify the directory to apply the
                # path to at index 0 and the actual path at index 1
                # e.g. ('llvm', 'patches/LLVM-uniqueptr-fix.patch'))
                original_dir = os.getcwd()
                patch_dir, path = path
                os.chdir(patch_dir)

            if "/" not in path:
                path = f"{config_path}/{path}-{self.version}.patch"
            apply_patch(ctx, path, 1)

            if original_dir:
                os.chdir(original_dir)
        os.chdir("..")

        os.makedirs("obj", exist_ok=True)
        os.chdir("obj")
        if self.commit or major_version >= 9:
            projects = ["clang"]
            if self.lld:
                projects.append("lld")
            runtimes = []
            if self.compiler_rt:
                runtimes.append("compiler-rt")
            run(
                ctx,
                [
                    "cmake",
                    "-G",
                    "Ninja",
                    "-DCMAKE_INSTALL_PREFIX=" + self.path(ctx, "install"),
                    "-DLLVM_BINUTILS_INCDIR="
                    + self.binutils.path(ctx, "install/include"),
                    f"-DLLVM_ENABLE_PROJECTS={','.join(projects)}",
                    f"-DLLVM_ENABLE_RUNTIMES={','.join(runtimes)}",
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DLLVM_ENABLE_ASSERTIONS=On",
                    "-DLLVM_OPTIMIZED_TABLEGEN=On",
                    "-DCMAKE_C_COMPILER=gcc",
                    # must be the same as used for compiling passes
                    "-DCMAKE_CXX_COMPILER=g++",
                    *self.build_flags,
                    "../src/llvm",
                ],
            )
        else:
            run(
                ctx,
                [
                    "cmake",
                    "-G",
                    "Ninja",
                    "-DCMAKE_INSTALL_PREFIX=" + self.path(ctx, "install"),
                    "-DLLVM_BINUTILS_INCDIR="
                    + self.binutils.path(ctx, "install/include"),
                    "-DCMAKE_BUILD_TYPE=Release",
                    "-DLLVM_ENABLE_ASSERTIONS=On",
                    "-DLLVM_OPTIMIZED_TABLEGEN=On",
                    "-DCMAKE_C_COMPILER=gcc",
                    # must be the same as used for compiling passes
                    "-DCMAKE_CXX_COMPILER=g++",
                    *self.build_flags,
                    "../src",
                ],
            )
        run(ctx, f"cmake --build . -- -j {ctx.jobs}")

    def install(self, ctx: Context) -> None:
        os.chdir("obj")
        run(ctx, "cmake --build . --target install")

    def is_fetched(self, ctx: Context) -> bool:
        return os.path.exists("src")

    def is_built(self, ctx: Context) -> bool:
        return os.path.exists("obj/bin/llvm-config")

    def is_installed(self, ctx: Context) -> bool:
        if not self.patches:
            # allow preinstalled LLVM if version matches
            # TODO: do fuzzy matching on version?
            proc = run(ctx, "llvm-config --version", allow_error=True)
            if proc and proc.returncode == 0:
                installed_version = proc.stdout.strip()
                if installed_version == self.version:
                    return True
                else:
                    ctx.log.debug(
                        f"installed llvm-config version {installed_version} "
                        f"is different from required {self.version}"
                    )

        return os.path.exists("install/bin/llvm-config")

    def configure(self, ctx: Context) -> None:
        """
        Set LLVM toolchain programs in **ctx**. Should be called from the
        ``configure`` method of an instance.

        :param ctx: the configuration context
        """
        ctx.cc = "clang"
        ctx.cxx = "clang++"
        ctx.ar = "llvm-ar"
        ctx.nm = "llvm-nm"
        ctx.ranlib = "llvm-ranlib"
        ctx.cflags = []
        ctx.cxxflags = []
        ctx.ldflags = []

    @staticmethod
    def add_plugin_flags(
        ctx: Context, *flags: Iterable[str], gold_passes: bool = True
    ) -> None:
        """
        Helper to pass link-time flags to the LLVM gold plugin. Prefixes all
        **flags** with ``-Wl,-plugin-opt=`` before adding them to
        ``ctx.ldflags``.

        :param ctx: the configuration context
        :param flags: flags to pass to the gold plugin
        """
        for flag in flags:
            if gold_passes:
                ctx.ldflags.append("-Wl,-plugin-opt=" + str(flag))
            else:
                ctx.ldflags.append("-Wl,-mllvm=" + str(flag))


@dataclass
class LLVMBinDist(Package):
    """
    LLVM + Clang binary distribution package.

    Fetches and extracts a tarfile from http://releases.llvm.org.

    :identifier: llvm-<version>
    :param version: the full LLVM version to download, like X.Y.Z
    :param target: target machine in tarfile name, e.g., "x86_64-linux-gnu-ubuntu-16.10"
    :param suffix: if nonempty, create {clang,clang++,opt,llvm-config}<suffix> binaries
    """

    version: str
    target: str
    bin_suffix: str

    def ident(self) -> str:
        return "llvmbin-" + self.version

    def is_fetched(self, ctx: Context) -> bool:
        return os.path.exists("src")

    def fetch(self, ctx: Context) -> None:
        ident = f"clang+llvm-{self.version}-{self.target}"
        tarname = ident + ".tar.xz"
        download(ctx, f"http://releases.llvm.org/{self.version}/{tarname}")
        run(ctx, ["tar", "-xf", tarname])
        shutil.move(ident, "src")
        os.remove(tarname)

    def is_built(self, ctx: Context) -> bool:
        return True

    def build(self, ctx: Context) -> None:
        pass

    def is_installed(self, ctx: Context) -> bool:
        return os.path.exists("install")

    def install(self, ctx: Context) -> None:
        shutil.move("src", "install")
        os.chdir("install/bin")

        if self.bin_suffix:
            for src in ("clang", "clang++", "opt", "llvm-config"):
                tgt = src + self.bin_suffix
                if os.path.exists(src) and not os.path.exists(tgt):
                    ctx.log.debug(f"creating symlink {tgt} -> {src}")
                    os.symlink(src, tgt)
