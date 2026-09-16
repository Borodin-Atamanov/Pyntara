# Sotavpn pool of the local proxy

This spec covers the sotavpn_setup task: the paid Sota Connect account of
the source vault becomes a pool of remote exits of the 3x-ui panel, and the
fastest member of the pool carries the traffic of the machine. The task is
the second source of remote exits. It runs after three_x_ui_xray_setup,
which makes this machine a client of one remote server ([3x-ui](3x-ui.md)),
and it adds the pool to the policy of that client without rebuilding the
policy.

## The account and the bridge

The access key of the account is the password of the source vault entry
named by key_entry_title. The source vaults of the fresh clone are the only
source, opened with the run password the way local_vault_setup opens them;
an unavailable vault, a missing entry and an empty password all mean the
pool is not configured for this run, and the task reports that and changes
nothing.

The server list is served by the bridge program of the Sotavpn repository
(https://github.com/Borodin-Atamanov/sotavpn-subscription-for-any-client).
The task downloads the branch archive named by archive_url into a temporary
directory, and the installer of the archive runs through the configured
user wrapper, so the bridge is installed in user mode for the desktop
account: its program and settings live under the home directory of that
account and it runs as a user service. The session environment the engine
reads for that account goes into the environment of the installer, so the
user manager is reachable from a run that has no session of its own.

The installer runs only when the version of the archive settings differs
from the version of the installed settings or the user service is not
active; force mode runs it anyway. After that the task waits until the user
service is active and the port of the bridge settings has a listener, so
the panel fetch of the same run finds the bridge up.

## The panel side

The panel subscribes to the subscription address of the bridge, which
carries the access key and asks for the raw answer: the list of vless links
the panel turns into outbounds whose tags begin with the configured tag
prefix. The subscription is created or updated by its remark, so a rerun
with a new port, a new prefix or a new interval converges instead of
failing on a duplicate, and a subscription that already carries the wanted
values is left alone. Every run refreshes the subscription, so the node
list is fetched from the bridge right away; the task then reads how many
nodes the subscription carries and names the fetch error of the panel when
one is recorded. allow_private of the subscription is what lets the panel
fetch from the loopback address of the bridge.

## The pool

The task writes two objects into the stored Xray template: one observatory
that measures the members with the configured probe and one least-ping load
balancer named by balancer_tag. The selector of both covers the tag prefix
of the subscription and the remote outbound tag of the
[three_x_ui_xray_setup] table, so the Sota nodes and the remote server of
this machine compete in one pool. The core matches a selector entry by
prefix and excludes from a balanced strategy every member its observatory
does not observe, so the two selectors are kept equal. The balancer keeps
the remote outbound as its fallback, so a pool without an available member
still leaves the connection with the server of the client setup.

The rules that send the remote classes of the policy to the remote outbound
are repointed to the balancer while their match is left as it is: the
classes the policy decided keep their decision, and the pool picks the
member. The rewrite is a pure function of pyntara.routing_policy and is
idempotent, so a later run of three_x_ui_xray_setup finds the pool and
points its own rules at the balancer as well (the routing check of that
task accepts any member the balancer selector covers).

## A machine without the remote outbound

The remote server itself carries no remote outbound: three_x_ui_xray_setup
leaves the client half of that machine unconfigured. Such a machine gets
the pool over the subscription nodes only, without the fallback, and no
rule is rewritten. The client half of that machine is owned by
three_x_ui_xray_setup and this task never builds it: here the pool is
added, the policy is not.

## Secrets

The access key appears only inside the subscription address the panel
stores. The task never publishes it: every message that could carry it (a
panel answer, a recorded fetch error) passes through a mask that replaces
the key before the message reaches the log or the terminal.

## Idempotency and warnings

A run whose pool is already in place installs nothing, writes no
subscription and writes no template, and reports done with no changes.
Every step that could not be reached is a warning of a completed task, so
one dead step leaves the rest of the machine configured: a bridge that does
not answer, an installer that failed, a panel that could not fetch the
list, a running core that does not report the balancer yet. The task
belongs to the default sets of the server and desktop modes, so the check
of the key runs on every installation and a machine without the entry
simply reports the pool as not configured.
