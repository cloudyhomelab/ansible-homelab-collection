# binarycodes\.homelab Release Notes

**Topics**

- <a href="#v1-0-0">v1\.0\.0</a>
    - <a href="#release-summary">Release Summary</a>
    - <a href="#minor-changes">Minor Changes</a>

<a id="v1-0-0"></a>
## v1\.0\.0

<a id="release-summary"></a>
### Release Summary

First release\. Extracted from the playbook repository it grew up in\, with the repository\-specific parts removed\.

<a id="minor-changes"></a>
### Minor Changes

* Filters <code>secret\_digests</code>\, <code>reconcile\_secrets</code>\, <code>route\_problems</code>\, <code>container\_problems</code>\, <code>systemd\_env\_lines</code> and <code>manifest\_units</code>\, callable independently of the role\.
* Molecule scenario covering both kinds\, idempotence\, the manifest prune\, a change of kind\, the secrets path end to end \- decrypted\, stored\, rotated\, dropped and re\-stored after drift \- and a repeated decommission that stops what an app is running whether or not the call names its units\.
* The <code>systemd\_app</code> role deploys or decommissions one app\, either from a controller directory of Quadlet files\, systemd units and config \(<code>source</code>\) or as a single\-container Quadlet rendered from call\-site parameters \(<code>inline</code>\)\; install\-manifest reconciliation across both kinds\; SOPS\-encrypted podman secrets with digest\-based rotation\; an optional Caddy route\; pre\-created bind\-mount directories\; healthcheck\-gated <code>podman auto\-update</code> rollback\.
