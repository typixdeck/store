# TypixDeck catalog icons

`manifest.json` maps each exact Debian package name to a local asset filename,
icon-theme name, byte size, SHA-256, source and license provenance. The Store
ships these assets so icons work offline even before an application is installed.
Store loads catalog artwork from its private directory. Only Store's own icon
is exported under hicolor; each installed application owns its desktop icons.
This separation avoids cross-package file conflicts during installation and
Store self-updates.

The 15 C1Max suite PNGs and existing Copilot SVG are copied byte-for-byte;
their original sources and generation prompts remain linked in the manifest.
No new license is assigned to inherited artwork (`NOASSERTION`). The six new
TypixDeck vector illustrations have the accompanying MIT asset license.
WeChat/MyAI illustrations identify community adapters; they do not claim to be
vendor-provided artwork or an official client.

All vectors use local paths, gradients and shapes only: no external URLs,
JavaScript, embedded remote resources or user data. PNG originals retain their
transparent backgrounds. Runtime image loading must request the required size,
not keep every full-resolution source in memory.
