# keyring_setup

Goal: after provisioning, a machine that logs in automatically never shows a
dialog that asks for the password of a keyring.

## Why the dialog appears

A login that types no password hands no password to PAM. `/etc/pam.d/sddm`
carries `pam_gnome_keyring.so` and `pam_kwallet5.so` with `auto_start`, but
the file of the automatic path, `/etc/pam.d/sddm-autologin`, carries no
keyring module at all: its authentication is `pam_permit.so`. The Secret
Service of the session, owned by `gnome-keyring-daemon`, therefore starts
without a password, its login collection stays protected by a password that
was never typed, and the first program that stores or reads a secret opens a
prompt. The user cannot answer the prompt, and it returns at every start.

The KDE wallet needs no action of its own on Kubuntu 26.04: `kwalletd6`
builds a `SecretServiceClient` backend and serves the `org.kde.KWallet`
calls over the Secret Service, so one store holds the secrets of Chrome, of
VS Code and of the KDE programs, and the `changePassword` call of that
interface does nothing without the KSecret backend.

## What the task does

The task gives the login collection of the Secret Service, the collection
the alias `default` names, an empty master password. A machine under full
disk encryption can afford it: the collection stays a private file of the
user, and an empty password means the collection opens at once instead of
asking.

The state of a session decides the step, and the client of the task runs both
of them as the desktop user on the session bus of that user:

When the alias `default` names no collection, the client creates the login
collection with an empty master password through
`org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface.CreateWithMasterPassword`,
which is the only creation call that takes a password instead of opening a
prompt. The label of the collection is the value `login`, in lowercase: the
identifier of a collection is built from its label, and the login alias of
gnome-keyring points at the identifier `login`. A label of `Login` would
create a second keyring named `Login.keyring` and leave the login collection
alone. The client reads the alias back after the creation, so a creation that
did not become the default collection is reported as an error and not as a
success.

When the alias names a collection, the client calls
`UnlockWithMasterPassword` with an empty password. That call opens no prompt
whatever the outcome, so it is the state check and the action in one. An
answer means the password of the collection is already empty, which is the
target state on this and on every later start of the daemon, because a
collection without a password is opened by the ordinary `Unlock` of any
program without a dialog. A refusal means the collection is protected by a
real password: it is left untouched and reported as a warning, because
replacing it would throw away every secret it holds. Nothing can open such a
collection without the password, and the user can still do it by hand.

A collection is reported as locked after every start of the daemon, even when
its password is empty, so `Locked` is never the signal of the target state.
The signal is the empty password that `UnlockWithMasterPassword` accepts.

## Packages and limits

The task installs `python3-gi`, the binding the client imports, through the
shared package install path.

A machine without a live desktop session is left untouched and reported: the
secret service of a session exists only inside that session, so a server or a
machine at the login screen has no collection to change. The task does not
edit `/etc/pam.d/sddm-autologin` and does not touch a collection protected by
a password.

## Configuration

Values live in `src/pyntara/values/keyring_setup.py`:

`packages` - the binding the client needs
`runuser_command` - the wrapper that runs a command as the desktop user
`python_script_command` - the interpreter of the client, with the placeholder `python`
`client_script_file_name` - the client under `task_data/keyring_setup/`
`bus_name`, `service_object_path`, `service_interface_name`, `internal_interface_name` - the names of the secret service and of the internal creation interface
`label_property` - the property that carries the label of a collection
`login_collection_label` - the label of the login collection, lowercase
`default_alias` - the alias that names the default collection of a session
`session_algorithm`, `secret_content_type` - the plain session and the content type of the master password
`outcome_key`, `detail_key`, `outcome_created`, `outcome_already_passwordless`, `outcome_protected`, `outcome_error` - the answer protocol between the client and the task
