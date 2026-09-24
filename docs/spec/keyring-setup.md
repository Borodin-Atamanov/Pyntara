# keyring_setup

Goal: after provisioning, a machine that logs in automatically never shows a
dialog that asks for the password of a keyring.

## Why the dialog appears

A login that types no password hands no password to PAM: the PAM service of
the automatic path carries no keyring module at all. The Secret Service of the
session therefore starts without a password, its login collection stays
protected by a password that was never typed, and the first program that
stores or reads a secret opens a prompt the user cannot answer.

Which store a program uses is the program's own choice, not this task's.
Chrome and the programs built on Electron take their encryption key from the
KDE wallet in a KDE session, while programs that speak the freedesktop Secret
Service use the secret service. The KDE wallet is a store of its own and this
task does not touch it.

## What the task does

The task gives the login collection of the Secret Service an empty master
password. A machine under full disk encryption can afford it: the collection
stays a private file of the user, and an empty password means it opens at once
instead of asking.

The task runs as the desktop user inside the live session, because the secret
service exists only inside that session, and it decides by the state it finds.

A collection that opens without a password is the target state, and nothing is
written.

A collection the login opened is left alone: its password is in use and
nothing asks for it.

A collection that is protected, that the login did not open and that holds no
items is replaced. Its file goes to the trash of the user and the collection is
created again without a password. The login that did not open it is the signal
that the machine logs in automatically, and an empty collection is the signal
that there is nothing to lose.

A collection that holds items is left untouched and reported as a warning.
Replacing it would throw the secrets away, and nothing can open it without the
password it carries.

No resource is ever removed for good: a file the task replaces goes to the
trash of the user. A machine without a live session is left untouched and
reported.

## Configuration

Values live in `src/pyntara/values/keyring_setup.py`: the binding the client
imports, the wrapper that runs a command as the desktop user, the interpreter
of the client, the name of the client under `task_data/keyring_setup/`, the
names of the secret service, the labels and aliases of the collections, the
trash command, and the vocabulary of the answer the client prints.
