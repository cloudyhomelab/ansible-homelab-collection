# binarycodes\.homelab Release Notes

**Topics**

- <a href="#v1-1-1">v1\.1\.1</a>
    - <a href="#release-summary">Release Summary</a>
- <a href="#v1-1-0">v1\.1\.0</a>
    - <a href="#release-summary-1">Release Summary</a>
    - <a href="#major-changes">Major Changes</a>
    - <a href="#minor-changes">Minor Changes</a>
    - <a href="#breaking-changes--porting-guide">Breaking Changes / Porting Guide</a>
    - <a href="#deprecated-features">Deprecated Features</a>
    - <a href="#security-fixes">Security Fixes</a>
    - <a href="#bugfixes">Bugfixes</a>
    - <a href="#known-issues">Known Issues</a>
    - <a href="#new-plugins">New Plugins</a>
        - <a href="#filter">Filter</a>
    - <a href="#new-modules">New Modules</a>
- <a href="#v1-0-0">v1\.0\.0</a>
    - <a href="#release-summary-2">Release Summary</a>
    - <a href="#minor-changes-1">Minor Changes</a>

<a id="v1-1-1"></a>
## v1\.1\.1

<a id="release-summary"></a>
### Release Summary

Nothing in the collection changes\: the role\, filters and modules are those of 1\.1\.0\. This release exercises the release procedure itself\, which is now driven from a pull\-request label and two approvals rather than scripts run by hand\.

<a id="v1-1-0"></a>
## v1\.1\.0

<a id="release-summary-1"></a>
### Release Summary

The role\'s host\-side work moves into two modules\. <code>podman\_secrets</code> reconciles an app\'s secrets from ownership and digest labels on the secrets themselves\, and <code>install\_manifest</code> reconciles the install record on the host\; both support check and diff mode\. Read the major change before upgrading\: the first deploy re\-creates every app\'s secrets to label them and restarts each app with secrets once\. The controller floor rises to ansible\-core 2\.19 and the host floor to podman 4\.5\. New <code>inline</code> parameters set the <code>AutoUpdate\=</code> and <code>Restart\=</code> policy\, the role refuses a domain another app\'s route already claims\, and the filters the modules replace are deprecated ahead of 2\.0\.0\.

<a id="major-changes"></a>
### Major Changes

* On the first deploy after upgrading\, the <code>systemd\_app</code> role removes and re\-creates every podman secret an app declares\, so that each carries its ownership and digest labels\, and restarts the app once\. Secrets stored by earlier releases carry no labels\, so the role cannot tell whether their values still match and treats them as changed\. Plan for one restart per app with secrets\.

<a id="minor-changes"></a>
### Minor Changes

* Decommissioning a <code>source</code> app no longer requires <code>systemd\_app\_apps\_dir</code>\. A decommission works from the host alone and never read the controller\'s tree\; the requirement was a leftover of the deploy path\'s\.
* New filter <code>app\_validation\_errors</code>\: why an app cannot be acted on as named\, one string per problem \- the name\'s shape\, the kind and state\, the image an <code>inline</code> deploy requires\, the <code>systemd\_app\_apps\_dir</code> a <code>source</code> app requires and the directory under it a <code>source</code> deploy reads from\, the shape of each <code>systemd\_app\_data\_dirs</code> entry\, and the names in <code>secrets\.sops\.yaml</code> \(never the values\)\. The <code>systemd\_app</code> role now validates its input through it\, in one task\, reporting every problem at once\.
* New filter <code>source\_tree</code>\: what a <code>source</code> app ships\, read from its directory on the controller \- the files of <code>quadlet/</code> and <code>unit/</code>\, every file of <code>config/</code> \- and the host path each installs to\. The <code>systemd\_app</code> role now records its install manifest from it instead of composing the paths in the play\.
* New module <code>install\_manifest</code>\: keeps an app\'s install manifest on the host\, pruning what the last deploy installed and this one does not\, recording what this one did\, and removing everything recorded on decommission\. Every recorded path is checked against the app\'s install roots first\, and the units the record implies are returned so a decommission need not be told them\. Supports check and diff mode\. The <code>systemd\_app</code> role now uses it\; the record format is unchanged\.
* New module <code>podman\_secrets</code>\: reconciles one app\'s podman secrets against the store in one call\, creating what is missing\, rotating what changed\, removing what the app stopped declaring\, and refusing a name another app owns\. Ownership and a digest of each value \(<code>sha256\:\<hex\></code>\) are recorded as labels on the secret itself\, so the store is the only record and a secret removed by hand is re\-created on the next run\. Supports check mode and diff mode\; no return value carries a secret\. The <code>systemd\_app</code> role now uses it for its secrets\.
* The <code>podman\_secrets</code> module gained <code>adopt\_file</code>\: a file on the host whose JSON object\'s keys are added to <code>adopt</code>\. The <code>systemd\_app</code> role passes the record releases before 1\.1\.0 wrote\, <code>/var/app/\<app\>/\.secret\-digests</code>\, instead of reading it on the controller with <code>slurp</code> and decoding it in Jinja\. A record that is not a JSON object now fails the decommission with the file named and what to do about it\, where before it failed with a template traceback\.
* The <code>systemd\_app</code> role decrypts an app\'s <code>secrets\.sops\.yaml</code> through the <code>community\.sops\.sops</code> lookup\, evaluated where the values are used\, instead of loading them into a fact that persisted for the rest of the play\.
* The <code>systemd\_app</code> role gained <code>systemd\_app\_auto\_update</code> and <code>systemd\_app\_restart</code>\, which set an <code>inline</code> app\'s <code>AutoUpdate\=</code> and <code>Restart\=</code> directives\. The defaults\, <code>registry</code> and <code>always</code>\, render exactly what earlier releases did\; <code>local</code> follows a locally built image\, and <code>never</code> writes no <code>AutoUpdate\=</code> line\, for an image pinned by digest\. Both are checked against systemd\'s and podman\'s accepted values in the argument spec\, since neither program rejects an unknown value when the unit is loaded\.
* The <code>systemd\_app</code> role now refuses to deploy an app whose <code>systemd\_app\_domain</code> is already the address of a site block in another app\'s route snippet under <code>systemd\_app\_caddy\_confd</code>\, naming that file\. Two site blocks with one address make Caddy refuse the whole imported config\, taking every app\'s route down\; the refusal happens before anything is installed on the host\.
* The <code>systemd\_app</code> role reconciles an app\'s podman secrets before installing any of its files\, so a value the store refuses fails the deploy with nothing on the host\, as a refused name already did\.

<a id="breaking-changes--porting-guide"></a>
### Breaking Changes / Porting Guide

* The <code>systemd\_app</code> role now needs podman 4\.5 or newer on the host\, where <code>podman secret create</code> learned <code>\-\-label</code>\; the previous floor was 4\.4\. Every platform the role claims ships podman 5\.x\.
* The collection now requires ansible\-core 2\.19 or newer on the controller\; the previous floor was 2\.15\. The floor is what the oldest claimed platform\, Debian 13\, ships\, and 2\.15 through 2\.18 are end of life upstream\. A controller on an older ansible\-core can no longer install the collection\.

<a id="deprecated-features"></a>
### Deprecated Features

* The <code>manifest\_units</code> filter is deprecated and will be removed in 2\.0\.0\. The <code>install\_manifest</code> module returns the same names as <code>units</code>\.
* The <code>route\_problems</code> and <code>container\_problems</code> filters are renamed <code>route\_validation\_errors</code> and <code>container\_validation\_errors</code>\. The old names redirect to the new ones with a deprecation warning and are removed in 2\.0\.0\.
* The <code>secret\_digests</code> and <code>reconcile\_secrets</code> filters are deprecated and will be removed in 2\.0\.0\. The <code>podman\_secrets</code> module does their work from labels on the secrets themselves\; the role no longer calls either\.

<a id="security-fixes"></a>
### Security Fixes

* A failed <code>podman secret create</code> now reports the secret\'s name\, podman\'s exit code and its stderr\. Previously the whole task result was censored along with the value\, so a full store or a permission problem gave the operator nothing to go on\.
* The <code>systemd\_app</code> role no longer writes <code>/var/app/\<app\>/\.secret\-digests</code>\, a root\-only file holding an unsalted SHA\-256 of every secret the app declared\. A copy of the app\'s directory \- a backup\, a snapshot \- carried a dictionary\-attackable digest of each secret\; the digest now sits as a label beside the secret in podman\'s own store\, where root already has the value\. An existing record file is removed on the next deploy\.
* The <code>systemd\_app</code> role now refuses an install manifest line that names a directory and removes nothing\. Previously such a line passed the path checks and reached <code>file\: state\=absent</code>\, which removes recursively \- a <code>\.wants</code> directory inside <code>/etc/systemd/system</code>\, or a subtree of the app\'s config\, was within reach of a tampered record\. Pruned paths are now unlinked\, never removed recursively\.

<a id="bugfixes"></a>
### Bugfixes

* A symlink to a file under a <code>source</code> app\'s <code>config/</code> is now recorded in the install manifest\. The copy that deploys the tree follows it and installs a regular file\, but the search that listed the tree did not\, so the file was never pruned once the app stopped shipping it\.
* The <code>systemd\_app</code> role no longer reports storing every declared podman secret when run under <code>\-\-check</code>\. The store listing it reconciles against is a read\-only command\, which Ansible skipped in check mode\, leaving the role unable to distinguish an empty store from an unasked question\; it now runs in check mode too\. Storing and removing a secret still cannot be previewed\, and the role\'s README says so\.
* The <code>systemd\_app</code> role now disables a plain unit before removing its file\, both when a deploy stops shipping it and on decommission\. Previously the <code>\.wants</code> symlink that enabling it had created was left behind\, and <code>systemctl</code> reported the unit as <code>not\-found</code> for good\. The <code>install\_manifest</code> module returns the units a prune implies as <code>pruned\_units</code> so the role can ask before the files go\.
* The <code>systemd\_app</code> role now refuses a <code>systemd\_app\_name</code> with a trailing newline\. The check was a <code>\$</code>\-anchored <code>match</code>\, which accepts one\, and nothing the name is written into \- install paths\, <code>ContainerName\=</code>\, the route snippet\'s filename \- fails on it\. No working deploy is affected\, since such a container would never have started\.

<a id="known-issues"></a>
### Known Issues

* The <code>systemd\_app</code> role\'s platform list now names only what its molecule scenario converges\: Fedora 43 and Debian 13 \(trixie\)\. EL and Ubuntu are no longer listed\; they were never tested\, and Debian 12 is out because its podman \(4\.3\) predates Quadlet\. The role is not distribution\-specific and other platforms will likely work\, but they are not claimed until something tests them\.

<a id="new-plugins"></a>
### New Plugins

<a id="filter"></a>
#### Filter

* binarycodes\.homelab\.app\_validation\_errors \- Why an app cannot be deployed or decommissioned as named\, one string per problem\.
* binarycodes\.homelab\.container\_validation\_errors \- Why an app\'s Quadlet cannot be rendered\, one string per problem\.
* binarycodes\.homelab\.route\_validation\_errors \- Why an app cannot be routed\, one string per problem\.
* binarycodes\.homelab\.source\_tree \- What a <code>source</code> app ships\, and the host paths it installs to\.

<a id="new-modules"></a>
### New Modules

* binarycodes\.homelab\.install\_manifest \- Reconcile the files an app installed against the record of its last deploy\.
* binarycodes\.homelab\.podman\_secrets \- Reconcile one app\'s podman secrets against the store\.

<a id="v1-0-0"></a>
## v1\.0\.0

<a id="release-summary-2"></a>
### Release Summary

First release\. Extracted from the playbook repository it grew up in\, with the repository\-specific parts removed\.

<a id="minor-changes-1"></a>
### Minor Changes

* Filters <code>secret\_digests</code>\, <code>reconcile\_secrets</code>\, <code>route\_problems</code>\, <code>container\_problems</code>\, <code>systemd\_env\_lines</code> and <code>manifest\_units</code>\, callable independently of the role\.
* Molecule scenario covering both kinds\, idempotence\, the manifest prune\, a change of kind\, the secrets path end to end \- decrypted\, stored\, rotated\, dropped and re\-stored after drift \- and a repeated decommission that stops what an app is running whether or not the call names its units\.
* The <code>systemd\_app</code> role deploys or decommissions one app\, either from a controller directory of Quadlet files\, systemd units and config \(<code>source</code>\) or as a single\-container Quadlet rendered from call\-site parameters \(<code>inline</code>\)\; install\-manifest reconciliation across both kinds\; SOPS\-encrypted podman secrets with digest\-based rotation\; an optional Caddy route\; pre\-created bind\-mount directories\; healthcheck\-gated <code>podman auto\-update</code> rollback\.
