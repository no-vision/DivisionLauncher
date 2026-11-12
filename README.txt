DIVISION LAUNCHER

Standalone Linux/Windows launcher: server selection, account/profile management,
private friends, game startup adapters, local addons, display settings and updates.
Connects to a server chosen by the player. No server implementation, content pack,
retail executable, account store or private operator configuration is required to
build it. Players provide their own compatible installed game and Linux Proton.

SOURCE
src/ contains four client projects: Core (shared client DTOs only), Launcher,
LauncherDesktop and LauncherDesktop.Tests. adapters/ contains the Python and
JavaScript adapters; tools/ contains build, dependency and packaging commands.
packaging/ contains platform launch templates. DivisionLauncher.slnx and the
source launch.sh / launch.ps1 entry points are at the repository root.
No sibling/private repository is a build dependency.
The DTOs retain their server JSON contracts; Lua addons reference NLua directly.
Admin/deployment tests stay with DivisionServer rather than the client test suite.

BUILD AND TEST
Install the pinned SDK from global.json, Python3.11+ and Node for adapter tests.
  python tools/build.py build
  python tools/verify.py
First restore needs NuGet; --offline --dotnet PATH --packages PATH uses an existing
cache. Normal restores use package locks. --refresh-locks is explicit and intended
only for reviewed dependency/project changes.

LINUX PACKAGE
  python tools/dependencies.py linux
  python tools/build.py publish-launcher --runtime linux-x64
  python tools/build.py bundle-launcher --runtime linux-x64

WINDOWS PACKAGE
  python tools/dependencies.py windows
  python tools/build.py publish-launcher --runtime win-x64
  python tools/build.py bundle-launcher --runtime win-x64
Dependency preparation downloads only the documented adapter dependencies and is
an explicit network operation. Existing prepared dependencies can be reused offline.
Linux bundles use system Python3; Windows bundles include embedded Python.
Both include self-contained .NET and third-party notices. Linux Python wheels
are version-pinned but not hash-pinned; see dependencies/linux.json.
Prepared adapter dependencies are cached in .cache/adapters/<platform>.
Bundles contain app/, platform launch scripts and, on Linux, adapter/.
Local settings use profiles/ beside the launch scripts. Updates retain the
profile location of an existing installation; no profile migration is required.

Outputs are in artifacts/launcher/<runtime> and artifacts/bundles. Generated
artifacts, downloaded dependencies, profiles, credentials and account databases
are ignored. Distribute verified bundles; do not archive the whole checkout.
Release GUI packages omit debug symbols.

PUBLICATION
  python tools/audit_publication.py
  python tools/audit_release.py --directory artifacts/launcher/linux-x64 --client-only
Keep real identities, captures and server content out of this public repository.
