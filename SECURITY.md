# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |

## Reporting a vulnerability

Please do not open a public issue. Use
[Report a vulnerability](https://github.com/nabrahma/RealIndex/security/advisories/new) on the Security tab
instead. You should hear back within a week.

Things worth reporting include a download that makes RealIndex write, move or delete files outside its library
folder, extracting a zip outside its cache (path traversal), or a download that gets Python code run in Blender's
UI process.

RealIndex opens downloaded files in background Blender processes started with `--factory-startup` and
`--disable-autoexec`, so scripts embedded in a downloaded .blend file are not run. Treat downloads from
untrusted sources with the same care as anywhere else.
