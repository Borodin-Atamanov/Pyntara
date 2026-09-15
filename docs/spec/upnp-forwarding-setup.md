# UPnP router port forwarding

There is a dedicated router port forwarding task: upnp_forwarding_setup.

The task deploys two systemd units. The oneshot service upnp_forwarding.service runs the module pyntara.upnp_forwarding from the shared deployment venv of system_metrics_setup with the single system config as its only argument, and the timer upnp_forwarding.timer runs that service after boot and then every configured interval. One run of the service makes the router in front of the machine publish the SSH port of this machine, and the timer repeats that run, so the rule survives a change of the address of the machine, a change of the address of the router and a reboot of the router. The reachability this adds is the direct one: a person connects to the address of the router on the published port and lands on the SSH daemon of the machine, with no server of the vault involved ([Port forwarding setup](port-forwarding-setup.md) covers the reverse tunnels, which work from anywhere but need those servers).

## Mechanism and its limits

The service drives the external upnpc client of the miniupnpc package, which the task installs, instead of implementing the UPnP IGD protocol. The client needs no credentials: it asks the router on the local network, so the mechanism works on any network whose router answers UPnP, and it does nothing at all on a network whose router does not.

The rules of the standard matter for the design, and the router this feature was proven on answers the oldest of them: UPnP IGD version 1. That version has no call that asks the router for a free port, no call that asks it for a range, no call that replaces a rule by name and no way to tell a free port from a taken one except by reading the whole rule table. Two behaviours of such a router shape the service: adding a rule for an external port that is already taken replaces the existing rule silently, and the rule table is readable in full, with the description of every rule. A router that answers only the modern calls therefore works as well, because the service uses the oldest common subset; a router without PCP or NAT-PMP, which would be the calls that reach through a provider NAT, is the normal case and not a problem.

## Port choice

The external port is the desired port of the machine, derived from the hostname by the same function the port_forwarding task uses over the range of the [port_forwarding_setup] table, so both schemes name the machine with the same predictable number and an operator learns one value per machine.

When another program already holds that port, the service tries the next candidate, and the candidates are hashes of the hostname with the attempt number appended: the second candidate is the desired port of `<hostname>2`, the third of `<hostname>3`, up to the configured number of attempts. Appending a number moves the attempt to another part of the range instead of walking the neighbours of the first port, where a dense block of foreign rules would waste the attempts. A hash that repeats an earlier candidate is dropped, so no attempt is spent on the same port twice. The published port is the first candidate the router accepted, and the machine therefore carries one number per network, not two.

## Ownership of a rule

A rule belongs to this project when its description is the configured one, upnp_mapping_description, which the router keeps and prints back in its rule table.

The service never touches a rule of another program. That matters because the router of the oldest standard replaces a rule silently instead of answering with an error: writing to a port is what takes it, so the decision to take a port must be deliberate, and the service sidesteps any port whose rule carries another description. A rule of this project whose target moved, which is what a changed address of the machine leaves behind, is written again: the router takes the new target over the old one, and the address the machine carries is read at every run.

## Telemetry

The System Metrics collector carries a upnp network module that runs the command pyntara.upnp_forwarding_state with the single system config. The command reads the rules of the router live through the same client and reports one record per rule of this project: the address of the router, the published port, the port of this machine the rule delivers to, the scope of the address and the ssh command that reaches the machine through it.

The rules are read from the router and not from a file the service writes, because the router is the source of truth: a rule that disappeared from the router disappears from the report in the same moment, and nothing has to be kept in step with a device that reboots. The scope tells how far the address reaches: a global address is one the internet routes to, while a router that itself sits behind another NAT reports an address of the provider network, which is reachable only from the networks that route to it. The record carries that scope, so a reader never mistakes a narrow address for a wide one and the address is not lost on the machine that cannot offer a global one. The telemetry PDF of the report picks the command up like every other ssh command of the report.

## Network changes

The service wakes the report collector when, and only when, it changed the rule on the router: the mapping was missing, or it pointed at another address or another port. That is the positive change a fresh report is worth, and the collector rebuilds the network report and the encrypted PDF, which the running System Metrics service then sends ([System Metrics](system-metrics.md), Availability changes).

A run that found the network unchanged wakes nobody, so a machine whose rule is already right sends nothing twice. A negative change, which is a rule that disappeared or a router that stopped answering, never wakes the collector either: the next scheduled report carries the current state anyway, so an immediate report would repeat what the schedule already tells.

## Update flow

The service reads the config once per run and re-derives everything from it: the desired port, the address of the machine, the address of the router, the rule table. A change of the config or a change of the network therefore needs no step beyond the next run of the timer, and a forced task run or a manual run of the service makes it immediate.

## Idempotency

The task is idempotent: it is done when both unit files match their templates and the timer is enabled and active. Otherwise it writes the units, reloads systemd, enables and starts the timer and runs the service once, so the rule exists at the end of a provisioning run and a broken deployment shows in the install log instead of surfacing at the first network change. Force mode rewrites the units and runs the service again.

The service itself is idempotent in the same sense: a run that finds the rule already right writes nothing to the router and wakes nobody.

The task follows the recoverable failure policy of the task contract: a missing template skips the write of that unit alone, and a failed write, daemon reload, enable, start or check is a warning of a completed task. A machine whose router does not answer UPnP, whose client package cannot be installed or whose every candidate port is taken by another program is a machine that is simply not reachable this way, which the service reports and never fails on.

## Parameters

All parameters live in the [upnp_forwarding_setup] table of the config/ directory: the package that provides the client and the binary the service runs, the protocol and the description of the rules this project owns, the number of ports the service tries, the names of the two units and their templates, the module and the command line the oneshot unit runs, the systemctl calls of the task, the boot delay and the interval of the timer, the journal identifier, the error priority, and the channel name with the two scope names the address carries in the report. The port range and the port formula are not repeated here: the service calls the shared function of pyntara.port_forwarding, which reads the range of the [port_forwarding_setup] table. The port of this machine is not configured at all: it is the sshd listen port of the ssh_daemon_setup directives, read through the shared ssh helper, so the rule and the daemon can never disagree.
