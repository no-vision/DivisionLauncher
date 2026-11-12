DIVISION LAUNCHER FOR WINDOWS

Extract the complete ZIP and run launch.cmd. Keep app/, launch.cmd and launch.ps1
together. The bundle includes the .NET runtime and Python adapter dependencies.
Provide your own compatible installed game, graphics drivers and prerequisites.

Add the server's HTTPS address. If it uses a privately signed certificate, obtain
its SHA256 fingerprint from the operator before saving the connection. Create an
account or sign in, select your installed game folder, and choose Play.
The server must advertise game service addresses reachable from this computer.
Inside a virtual machine, localhost refers to that virtual machine.

Windows launches through the bundled native adapter. It stages its own game
executable and isolates per-launch credentials and Documents under runtime/.
Original game files remain unchanged. Local settings and tutorial history use
profiles/. Keep that folder when upgrading; server accounts remain on the server.
An update installed into an older bundle retains that bundle's profile location.

Use the launcher's update controls to import a trusted release package. Installed
versions live in releases/ and the active selection is active-release.txt.
Returning to the bundled version preserves profiles and installed releases.

Optional in-game overlays require D3D11; F8 enables their controls. Lua addons go
in addons/. Install only scripts you trust. Server observation panels require
support from the selected server.

If startup fails, inspect runtime/<run>/client-adapter.log. Native Windows game
and graphics compatibility depends on the installed game and machine configuration.
