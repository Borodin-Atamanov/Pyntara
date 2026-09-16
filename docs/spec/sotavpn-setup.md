# Sotavpn subscription of the panel

This spec covers the sotavpn_setup task: the paid Sota Connect account of
the source vault becomes a source of remote exits for the local proxy of
this machine. The pool the traffic leaves through belongs to
three_x_ui_xray_setup ([3x-ui](3x-ui.md)), which builds the local proxy
and its pool on every machine; this task feeds that pool. It installs the
bridge that serves the server list and subscribes the panel to it, so the
nodes of the account appear among the outbounds the pool covers, and it
neither reads nor writes the routing policy.

## The account and the bridge

The access key of the account is the password of the source vault entry
named by key_entry_title. The source vaults of the fresh clone are the only
source, opened with the run password the way local_vault_setup opens them;
an unavailable vault, a missing entry and an empty password all mean the
subscription is not configured for this run, and the task reports that and
changes nothing.

The server list is served by the bridge program of the Sotavpn repository
(https://github.com/Borodin-Atamanov/sotavpn-subscription-for-any-client).
The task downloads the branch archive named by archive_url into a temporary
directory, and the installer of the archive runs through the configured
user wrapper, so the bridge is installed in user mode for the desktop
account: its program and settings live under the home directory of that
account and it runs as a user service. The session environment the engine
reads for that account goes into the environment of the installer, so the
user manager is reachable from a run that has no session of its own.

The installer runs on every run of the task, so the machine always runs the
code of the fetched branch: the same version is rewritten idempotently, and
the installer keeps the previous settings file beside the new ones and
restarts the user service. After that the task waits until the user service
is active and the port of the bridge settings has a listener, so the panel
fetch of the same run finds the bridge up.

## The panel side

The panel subscribes to the subscription address of the bridge, which
carries the access key and asks for the raw answer: the list of vless links
the panel turns into outbounds whose tags begin with the tag prefix, which
is pool_member_prefix of the [three_x_ui_xray_setup] table: the pool of the
local proxy covers every outbound whose tag begins with that prefix, so the
nodes of the account join the pool. The subscription is created or updated
by its remark, so a rerun with a new port, a new prefix or a new interval
converges instead of failing on a duplicate, and a subscription that
already carries the wanted values is left alone. Every run refreshes the
subscription, so the node list is fetched from the bridge right away; the
task then gives the panel the budget of subscription_fetch_wait_seconds to
turn that list into outbounds, reads how many nodes the panel reports for
the subscription, and names the fetch error of the panel when one is
recorded. A panel that reports no node yet is not an error: the task says
it in plain words, because the pool lives with or without members.
allow_private of the subscription is what lets the panel fetch from the
loopback address of the bridge.

## Where the nodes end up

The panel merges the outbounds of a subscription into the configuration it
builds on the next reconciliation, and its stored template never lists
them, so nothing here writes a template. The tag prefix is the only joint:
the pool of the local proxy covers the members by that prefix, its
observatory begins to measure them after that reconciliation, and the
remote classes then compete for the fastest member ([3x-ui](3x-ui.md),
The pool of the remote classes). The pool, its fallback and the machine
that is the remote server itself are the business of that task, not of
this one.

## Secrets

The access key appears only inside the subscription address the panel
stores. The task never publishes it: every message that could carry it (a
panel answer, a recorded fetch error) passes through a mask that replaces
the key before the message reaches the log or the terminal.

## Idempotency and warnings

A run whose bridge is installed and whose subscription already carries the
wanted values installs nothing and writes no subscription, and reports done
with no changes. Every step
that could not be reached is a warning of a completed task, so one dead
step leaves the rest of the machine configured: a bridge that does not
answer, an installer that failed, a panel that could not fetch the list, a
panel that reports no node yet. The task belongs to the default sets of
the server and desktop modes, so the check of the key runs on every
installation and a machine without the entry simply reports the
subscription as not configured.
