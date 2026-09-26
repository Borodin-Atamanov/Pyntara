# KDE settings fixes plan

Written 2026-09-26, following docs/guides/planning-procedure.md. Every fact below
was measured on the test stand (Kubuntu 26.04.1, KDE 6.6, Wayland) or read in the
source of KDE; a statement that is not backed by a measurement or a source is
marked as an assumption.

State on 2026-09-26: stages 1 to 3 are done (the two-phase client with the
registration call, the move of the per-layout combinations into the keyboard task
with the compositor restart after the keys, and the warning that names the
component, the action, the state and the holder). Stage 4 is done as well: the
same code ran on a configured machine and on a fresh machine, both runs reported
no shortcut warning, pressing Meta+E switched the layout to Spanish in each, and
the fresh machine kept the key after a reboot. Stage 5 remains: the other owners
that overwrite a written file (plasmashell over appletsrc, powerdevil over
powerdevilrc) and the reload of the appearance into the running session.

## Goal

The described goal: the desktop tasks of the desktop mode leave the machine with
the configured KDE settings in force.

The implied goal, which is the acceptance test: a user who runs the installer
and then, in the same session, presses the configured key or opens the panel
finds the configured behaviour, without a logout, without a machine reboot and
without opening a KDE settings dialog. A log message that a setting applies at
the next login is not a result; it is allowed only where no mechanism exists at
all, and it must name the cause and what was not applied.

## Bugs covered

Bug 1. The configured combination that switches to a layout does nothing on a
fresh machine. The run writes the entry into kglobalshortcutsrc and reports that
the daemon does not know the action.

Bug 2. The warning of the shortcut apply names no component and no reason:
src/pyntara/tasks/kde_settings.py lines 1241-1249 build "the daemon does not hold
the configured shortcuts: [(action, after, requested), ...], they are written
into the shortcut file for the next login", the tuples drop the component and the
friendly name through the zip unpacking, and no reason is reported.

Bug 3. A running KDE component writes its own memory back over a file the run
wrote. Measured 2026-09-19: one kwriteconfig6 --notify on kxkbrc made the running
components save their state (plasma-org.kde.plasma.desktop-appletsrc 19:15:27,
katerc 19:15:58, kglobalshortcutsrc 19:15:59, kwinrc 19:16:12), which dropped
values the run had written; a read-only audit of 335 configured desktop values on
a provisioned machine found ten that did not match, six of them wrong paths
inside the values themselves.

## Facts the plan rests on

1. On Wayland KWin is what switches the layout by key: it composes the action
   name "Switch keyboard layout to <long layout name>" and reads the combination
   of that action from the KGlobalAccel daemon when it starts (KDE/kwin,
   src/keyboard_layout.cpp, loadShortcuts, which asks the daemon through
   shortcutKeys). Measured consequence: a combination the daemon does not hold is
   not taken by KWin, and a compositor restart after the combination became live
   made the key work (pressed on the stand with ydotool, layout index 0 to 2,
   twice).
2. A line in kglobalshortcutsrc alone is inert. Measured: the daemon held no such
   action and answered an empty shortcut list, which is why the run reported "the
   daemon does not know these actions".
3. The daemon grants a combination only when nothing else holds it. Measured: the
   call for the layout action returned nothing while the Dolphin launch action
   held Meta+E; after taking the combination from Dolphin the daemon stored it
   (268435525) and the layout action owned the key.
4. Meta+E is a Kubuntu package default: /usr/share/applications/org.kde.dolphin.desktop
   carries X-KDE-Shortcuts=Meta+E, dpkg -V dolphin reports nothing and the file
   date is the package date.
5. The hotkey client skips a change whose action the daemon does not list
   (task_data/kde_keyboard_setup/apply_hotkeys.py, report["missing"] then
   continue). On a fresh machine no client has registered the per-layout actions,
   so this skip is exactly the failing case.
6. Order: kde_keyboard_setup writes the layouts and restarts the compositor,
   kde_settings writes and applies the shortcuts afterwards, so the restart
   happens before the keys exist.
7. The daemon refuses a combination for an action it was never told about, and
   the call that changes that is doRegister, the one the KGlobalAccel client
   library makes before it sets a shortcut. Measured on the stand on
   2026-09-26: without that call the daemon answered an empty key list for a
   per-layout action while the combination stayed with the launcher, and with
   the call the action held the combination. A registered action can still
   stay out of the list of its component, which only means the report has to
   say so instead of treating the state as unreached.
8. The daemon writes the applied state back into kglobalshortcutsrc itself, and a
   restart of the daemon makes it read the file: measured, the layout action then
   held a combination that a plain file edit alone had not given it. Writing
   through the daemon remains the reliable path, because the file entry loses to
   the action that already holds the combination (measured).

## Requirements

Functional:

1. After a run the configured per-layout combination switches the layout in the
   running session.
2. A combination the task claims is taken from whichever action holds it,
   including a package default of the distribution.
3. The setting survives a machine reboot.
4. A combination that could not be applied is reported with the component, the
   action, the reason the daemon gave and the state the action is in; the wording
   never presents applying at the next login as the outcome.
5. No substitute behaviour is configured: the combination switches to the
   configured layout, not to the next or the previous one.
6. Existing behaviour is kept: the layout list, the indicator display style,
   idempotency, a warning instead of a failure, and config writes without
   --notify, because a notified write is what made the owners save their memory.

Non-functional:

7. No new mechanism where an existing KDE call does the job.
8. At most one compositor restart per run, and only when the configuration
   changed, because the restart drops the windows the compositor owns.
9. The fix is proven by a real key press on a fresh machine, not only by unit
   tests.

## Scope

Changed: task_data/kde_keyboard_setup/apply_hotkeys.py,
src/pyntara/tasks/kde_keyboard_setup.py, src/pyntara/values/kde_keyboard_setup.py,
src/pyntara/values/kde_settings.py, the warning text in
src/pyntara/tasks/kde_settings.py, the tests of the keyboard task and of the
client, docs/spec/kde-keyboard-setup.md, docs/spec/kde-settings.md,
docs/TODO.md.

Untouched: the other tasks and their values, inst.sh and the bootstrap contract,
the metrics services, the repository layout.

## Approaches

Approach A, chosen. The client applies every change of its request, whether or
not the daemon lists the action, using the same takeover of the combination from
its current owner; the keyboard task owns the layout keys and applies them before
its single compositor restart; the kglobalshortcutsrc record of kde_settings for
those keys is removed. It uses the same call the System Settings shortcut dialog
uses, it makes the order explicit, and one restart closes the work.

Approach B, rejected. Restart the KGlobalAccel daemon after the file is written
so that it reads the new line. Measured: the file line loses to the action that
already holds the combination, so the key would still not be assigned, and the
approach adds a service restart without reaching the goal.

Approach C, rejected as the whole plan and kept as one call of it. Registering
the action with the daemon's doRegister was measured as insufficient on its own
(it does not make kwin take a key) and necessary inside the assignment (the
daemon refuses a combination for an action it was never told about), so the call
lives in phase two of Approach A.

## Detailed plan and decisions

Decision 1, a choice. The client applies a request in two phases and stops
checking whether the daemon lists the target action. Phase one collects the
combinations of the whole request and takes each of them from every action that
holds it, enumerating the holders with globalShortcutsByKey; phase two registers
each action of the request with doRegister, the call the KGlobalAccel client
library makes before it sets a shortcut, and then assigns the combination with
setForeignShortcutKeys. Freeing a
combination removes only that combination from a holder and keeps the holder's
other combinations, because clearing more would break unrelated shortcuts of
other applications. The shape follows from the daemon's interface: there is no
call that frees a combination, only calls that set the key list of one action,
so a holder lookup is unavoidable, and a lookup that names every holder is both
more complete and simpler than the owner check the client does today. The check
that the daemon knows the target action is dropped because it is not needed:
measured, a registered action accepts the combination whether or not the daemon
lists it under its component. The report keeps a flag that says whether the
daemon listed the action, so "assigned but not listed" stays distinguishable
from "assigned".

Decision 2, a choice. The keyboard task applies the layout keys. The values
layout_switch_shortcuts of that task carry the per-layout combinations instead of
the kglobalshortcutsrc record of kde_settings, whose value moved there, so one
task owns the layouts and their switching keys.

Decision 3, a choice. The compositor restart stays the last step of the keyboard
task and runs after the keys are assigned, keeping its current shape: the restart
through org.kde.KWin.replace, then starting the session manager, then polling the
real state instead of sleeping.

Decision 4, a choice. The warning of the shortcut apply names the component, the
action, the reason the daemon gave and the state after the last attempt, and it
does not present the next login as the outcome.

Decision 5, an assumption verified first. KWin takes a combination for an action
the daemon does not list. The probe on the stand: assign a combination to an
action the daemon does not list, restart the compositor, press the combination,
read the layout index. If the probe fails, the plan falls back to letting the
daemon learn the actions from its config first (measured path: the daemon then
holds the action), and the keyboard task gains that step.

Decision 6, needs the user. Bug 1 collides with the Kubuntu default of Dolphin on
Meta+E. Option A: the layout keeps Meta+E and the Dolphin entry is freed, which
is what the project's values already intend and what the owner takeover does.
Option B: the layout uses another combination, and the Dolphin default stays.

Decision 7, a choice. Bug 3 is handled per owner and measured per owner: the
compositor restart for kxkbrc and for the layout keys, a plasmashell restart for
appletsrc, a powerdevil restart for powerdevilrc, the plasma-apply tools for the
appearance, and starting the session services that stay inactive. Every owner
gets its own probe before its restart is wired in, because an unnecessary restart
costs the user windows.

## Reuse

The existing hotkey client, the existing compositor restart helpers and their
constants, the existing takeover of a combination from its owner, the existing
warning policy of the task contract, and ydotool, already installed on the stand,
for the key press of the live proof.

## Test coverage

Requirement 1: a live run on a fresh machine, then reading the layout index,
pressing the combination and reading it again; a unit test that the keys are
assigned before the restart command runs.
Requirement 2: a unit test of the client where another action holds the
combination and the client takes it.
Requirement 3: a reboot check on the fresh machine after the run.
Requirement 4: a unit test of the warning text: the component, the action, the
reason and the state are present, and the next login is not named as the outcome.
Requirement 5: a test that the rendered client receives exactly the configured
action names and combinations, so no substitute action can be configured.
Requirement 6: the existing task tests stay green, and a test that the config
writes carry no --notify.
Requirements 7 and 8: the gates and the review of the diff; one restart call, no
new daemon call beyond the existing client.
Requirement 9: stage 4 is the fresh-machine proof; unit tests cannot cover it.
Not covered by tests: a KDE version other than the one on the stand. The
specification records the measured version and the task reports the answer of the
daemon instead of assuming success.

## Stages

Stage 1, the first stage. The client change and its tests, plus two stand probes:
the combination of an action the daemon does not list is taken by KWin after a
compositor restart (Decision 5), and globalShortcutsByKey names every holder of a
combination, including a holder whose key comes from a packaged desktop entry. It
is the smallest step that tests the main risk.

Stage 2. The order and ownership change: the values move into the keyboard task,
the record leaves kde_settings, the task assigns the keys before the restart, and
the tests cover the order.

Stage 3. The warning text of the shortcut apply and its test.

Stage 4. The live proof on a fresh machine: the run, the key press, the state
read-back and the reboot check.

Stage 5. The remaining owners of Bug 3, each with its probe, then the spec
updates, the trimming of the TODO entry, the full gates and the merge into main.

## Risks

1. The assumption of Decision 5 fails. Mitigation: probe it in stage 1 before any
   code change, and use the measured fallback if it does not hold.
2. Taking Meta+E away from Dolphin surprises the user. Mitigation: the user
   decides between Option A and Option B; until then nothing is merged.
3. The compositor restart drops the windows the user has open. Mitigation: one
   restart, only when the configuration changed, at the end of the task.
4. A restart that a probe would show to be unnecessary costs windows for nothing.
   Mitigation: every owner is probed before its restart is wired in.
5. The daemon layer can differ between Plasma versions. Mitigation: the
   specification records the measured version, and the task reports the daemon's
   own answer instead of assuming success.
6. A verification run on a fresh machine takes about an hour. Mitigation: preload
   the guest package cache from the host cache and keep a qcow2 snapshot of the
   prepared guest.

## Open questions

1. Approval of this plan and of stage 1.
2. Decision 6: Option A or Option B.
