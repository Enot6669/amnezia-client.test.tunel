# Windows reverse split tunneling — driver implementation plan

This document is the implementation contract for the Windows-only reverse split-tunneling work in this fork.

## Required behavior

- Default route: every process uses the ordinary network path and does not enter the VPN tunnel.
- Allow-list: only processes whose normalized executable image path exactly matches a configured entry use the VPN tunnel.
- Android, iOS, macOS, Linux, and HTML deliverables are out of scope.
- The existing Windows exclusion mode must keep its current behavior.

## Pinned driver source

- Project: https://github.com/mullvad/win-split-tunnel
- Source tag: `v1.3.0.0`
- Source commit: `0a0eb97f67d1dbcb3d08bda66d3b24f465d95475`
- Existing signed binary package is not reverse-mode capable. It must not be represented as supporting this feature.

## Driver behavior found in the pinned source

The process registry uses `ST_PROCESS_SPLIT_STATUS_ON_*` to mean that the process is split out of the tunnel. The firewall then handles those processes as excluded applications. The configuration currently stores only exact image paths.

The process manager currently:
1. marks a process split when its image path is in the configured set;
2. otherwise inherits split state from its parent;
3. re-evaluates all registered processes when configuration changes, applying WFP changes in a transaction and rolling the transaction back on failure.

Reverse mode must invert the classification, not the meaning of the existing split status:
- configured exact image path => `ST_PROCESS_SPLIT_STATUS_OFF` (inside VPN);
- every other process => `ST_PROCESS_SPLIT_STATUS_ON_BY_CONFIG` (direct);
- no parent/ancestor inheritance in reverse mode.

The same classification must be applied to process arrivals and to the full process-registry synchronization path. WFP changes must remain transactional. The previous mode and configuration must be restored if the transaction fails.

## Protocol compatibility decision

Do not enlarge or reorder the existing `ST_CONFIGURATION_HEADER` used by `IOCTL_ST_SET_CONFIGURATION`: the old driver interprets entries immediately after that header and would misparse a larger header.

Implement a separate versioned reverse-mode IOCTL and payload (new IOCTL function number; legacy IOCTL remains unchanged). The Windows client should:
- continue using the legacy IOCTL for the existing exclusion mode;
- use the new IOCTL only for reverse mode;
- fail closed with a clear error if the installed driver does not recognize the new IOCTL.

The new IOCTL must update the image set and mode as one serialized operation. It must not expose an intermediate state where the new list is evaluated with the old mode or vice versa.

## Known upstream limitations requiring explicit treatment

- DNS requests commonly originate from the Windows `dnscache` service rather than the requesting application. The driver cannot reliably associate those requests with the originating executable. A design decision and tests are required before claiming per-application DNS isolation.
- The upstream driver documents localhost UDP bind-redirection limitations for excluded processes. Reverse mode makes most processes excluded, so loopback behavior must be investigated and tested rather than assumed to work.
- IPv4 and IPv6 must be tested independently.
- Existing connections need WFP reauthorization/transactional handling when the mode or allow-list changes.
- Kill Switch and tunnel-disconnect behavior must be tested to ensure selected applications do not silently fall back to direct networking.

## Build and packaging

- Build the modified driver from the pinned source using Visual Studio 2022 and the Windows Driver Kit.
- Development builds may be unsigned and require Windows test-signing configuration. Do not ship an unsigned driver as production-ready.
- Do not replace the existing Conan binary package with a custom driver until the custom x64 build has succeeded and its package contents have been verified.
- The Windows client installer is produced by the existing Windows build workflow as an `.exe`; APK and HTML outputs are not part of this task.

## Acceptance tests

1. Legacy exclusion mode still behaves exactly as before.
2. With reverse mode enabled and no selected applications, all ordinary applications use the direct route.
3. With one selected application, only that exact executable image uses the VPN route.
4. A selected child process remains classified by its own image path; it does not inherit the parent's route.
5. An unselected child of a selected process uses the direct route, and a selected child of an unselected process uses the VPN route.
6. Changing the allow-list updates already-running processes without a transient direct leak for selected apps.
7. New process arrivals during/after configuration changes receive the correct classification.
8. IPv4 and IPv6 both follow the same policy.
9. DNS behavior is measured and documented; no per-app DNS guarantee is claimed until the dnscache attribution issue is solved or a deliberate system-wide DNS policy is adopted.
10. Loopback TCP/UDP, LAN access, and localhost IPC are tested.
11. Kill Switch and VPN disconnect/reconnect do not cause selected applications to leak outside the tunnel.
12. Driver reset/uninstall leaves no active WFP filters or stale device state.
