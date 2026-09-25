# keyring_setup

Goal: after provisioning, a machine that logs in automatically never shows a
dialog that offers to create a secret store or asks for the password of one.

## Why the dialog appears

A login that types no password hands no password to PAM: the PAM service of the
automatic path carries no wallet module, and the helper the desktop starts at
login only forwards the environment of a module that never ran. Nothing creates
the KDE wallet at login, so the first program that asks for one starts its
creation, and the daemon answers that request with a dialog: the wallet is
missing, and the daemon offers a new wallet together with its password.

A wallet created that way is protected, so every later program asks for that
password again, and the user of a machine that never types one has none to
give. Which store a program uses is the program's own choice, not this task's;
the task owns the wallet of the session.

## What the task does

The task creates the wallet itself, before any program asks for it, and gives
it an empty password. A machine under full disk encryption can afford that: the
wallet stays a private file of the user and opens without a question.

The creation uses the entry point the PAM module of the wallet uses, which
takes a ready key instead of asking for a password, so the wallet is created
without any dialog. The key is the key of an empty password, derived exactly as
that module derives it: a random salt of 56 bytes next to the wallet, then
PBKDF2-HMAC-SHA512 over it with 50000 iterations and a 56 byte key. Every later
program derives the same key from the empty password, so the wallet opens
without a dialog for them as well.

The client runs as the desktop user on the session bus of that user, because
the wallet belongs to the session. The task renders the client next to its file
before the call, with the suffix of rendered_client_suffix and the mode of
rendered_client_file_mode of the engine values module, and the interpreter
receives the path of the rendered file, so the program text never travels as the
argument of a command line and one call keeps one line in the log of the run. It
asks the wallet service for the name of the wallet of the session, and that
question is also what starts the daemon that creates the wallet.

A wallet file that exists is left untouched and reported. A wallet may carry a
password, and asking it to open in order to find out is the call that shows the
dialog.

Force mode replaces the wallet. Every file of every wallet of the desktop user,
the wallet itself, the salt of its key and the cache of item attributes, goes
to the trash of that user, and the wallet is created again without a password.
That is the state a fresh installation plus a normal run would reach.

No resource is ever removed for good: the files of a replaced wallet go to the
trash of the user. A machine without a live session is left untouched and
reported, and force mode replaces nothing there either, because a wallet that
is missing would be created by the daemon with the dialog this task removes.

## Configuration

Values live in `src/pyntara/values/keyring_setup.py`: the binding the client
imports, the wrapper that runs a command as the desktop user, the name of the
client under `task_data/keyring_setup/`, the seconds a call may take, the names
of the wallet service and of its daemon, the constants of the key derivation,
the directory of the wallets and the endings of their files, the trash command
and the vocabulary of the answer the client prints.
