# Local recovery

There is deliberately **no administrator account and no remote password reset**
in SimpleUI. Every profile has equal application permissions and manages its own
data, and machine-wide changes are gated on the request coming from the computer
running SimpleUI — not on an account privilege (see
[ARCHITECTURE.md](ARCHITECTURE.md) "Profiles and privacy").

That leaves one recovery path, and it is a physical-access one: sit at the
computer running SimpleUI. This is not a weakening of the boundary. Anyone at
that keyboard can already read `data/app.sqlite3` and the ComfyUI output folders
directly, so the tool below grants nothing they did not already have. It is
intentionally **not reachable over HTTP**, not even from loopback.

## What can go wrong, and what fixes it

| Situation | Fix |
| --- | --- |
| Someone forgot their profile password | `set-password` |
| Sign-in is refused with "too many failed attempts" | Wait 15 minutes, or restart the SimpleUI server (the throttle is in memory) |
| A phone or tablet was lost and may still be signed in | `unlock` (clears that profile's sessions), then `set-password` |
| Everyone forgot every password, including Default | `set-password Default`, sign in, then reset the others |
| Multi-user mode should go away entirely | Remove the other profiles' data first, then `disable-multi-user` |

## Running it

From the repository, on the machine itself:

```cmd
cd backend
.venv\Scripts\python -m app.auth.recovery list
.venv\Scripts\python -m app.auth.recovery set-password "Alex"
.venv\Scripts\python -m app.auth.recovery unlock "Alex"
.venv\Scripts\python -m app.auth.recovery disable-multi-user
```

`list` shows every profile name, which one is Default, and whether it has a
password. `set-password` prompts twice without echoing and, on success, signs
that profile out of every device — so a lost phone stops working as soon as the
password is reset. `unlock` clears sessions without changing the password.

Stop the SimpleUI server first if you can. The commands are safe to run against
a live database (the same short transactions the app uses), but a running server
keeps serving already-issued cookies until it next reads the session table, and
the in-memory sign-in throttle only resets on restart.

### `disable-multi-user` refuses while other profiles exist

This is the same rule the API enforces, for the same reason: Default would
inherit a session that can reach the other profiles' workflows, history and
media. Sign in as each profile and export or delete its data first, then remove
the profile. The command is a recovery hatch, not an override.

## What recovery cannot do

- It cannot read anyone's existing password. Passwords are stored as salted
  scrypt hashes; resetting is the only option.
- It cannot decrypt or un-share media. Profile privacy here is application-level
  isolation among trusted home users, not hostile-tenant isolation: ComfyUI and
  its custom nodes still run with the host's filesystem privileges.
- It cannot be performed from a phone on the home network, over a reverse proxy,
  or by sending `X-Forwarded-For: 127.0.0.1`. Forwarded headers are never read
  (`backend/app/auth/security.py`); locality comes from the real socket peer.

## Backups

Recovery of *data*, as opposed to access, is a restore: use the SQLite backup
facility (`Database.backup`), never a raw copy of a live `.db` file, and keep a
manifest of the private files alongside it. Restores preserve IDs and ownership.
